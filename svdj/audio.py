"""FFmpeg-backed audio utilities for 随舞剪辑台.

The module deliberately uses only the Python standard library plus the
bundled ffmpeg executable, so the application can be shipped as a portable
folder on Windows.
"""

from __future__ import annotations

import array
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence


AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".ape",
    ".alac",
}

FORMAT_LABELS = {
    "mp3": "MP3",
    "wav": "WAV",
    "m4a": "M4A (AAC)",
    "flac": "FLAC",
}

# EBU R128-ish target used by the optional single-pass loudness filter.
LOUDNESS_TARGET = -14.0
LOUDNESS_PEAK = -1.5
LOUDNESS_RANGE = 11.0

_NOWINDOW = 0x08000000 if os.name == "nt" else 0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class AudioError(RuntimeError):
    """A user-presentable audio processing error."""


class AudioCancelled(RuntimeError):
    """Raised when a running export is cancelled by the user."""


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0


@dataclass(frozen=True)
class Waveform:
    duration: float
    peaks: tuple[float, ...]

    def peak_at(self, ratio: float) -> float:
        if not self.peaks:
            return 0.0
        index = min(len(self.peaks) - 1, max(0, int(ratio * len(self.peaks))))
        return self.peaks[index]


@dataclass
class ClipSpec:
    path: Path
    start: float
    end: float
    name: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class TransitionSpec:
    path: Path
    source_duration: float
    length: float = 3.0
    full: bool = False
    volume: float = 0.85
    fade: float = 0.2
    insert_between: bool = True
    at_end: bool = False

    @property
    def clip_length(self) -> float:
        base = self.source_duration if self.full else min(self.length, self.source_duration)
        return max(0.05, base)


@dataclass
class ExportPlan:
    tracks: list[ClipSpec]
    transition: TransitionSpec | None
    output_path: Path
    fmt: str = "mp3"
    bitrate: str = "320k"
    title: str = "随舞混剪"
    filter_script: str = ""
    total_duration: float = 0.0
    transition_count: int = 0
    normalize: bool = False
    _temp_path: Path | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Bundled tools


def app_root() -> Path:
    """Return the folder containing the application resources."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def find_ffmpeg() -> Path | None:
    """Locate ffmpeg, preferring the copy shipped with the application."""
    root = app_root()
    executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates: list[Path] = [
        root / "bin" / executable,
        root / executable,
        Path(sys.executable).resolve().parent / "bin" / executable,
    ]
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    # A developer convenience: make imageio-ffmpeg optional, never required.
    try:
        import imageio_ffmpeg  # type: ignore

        candidates.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
    except Exception:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def ffmpeg_path() -> Path:
    path = find_ffmpeg()
    if path is None:
        raise AudioError(
            "找不到 ffmpeg。请确认 bin\\ffmpeg.exe 与程序放在一起，"
            "或将 ffmpeg 加入系统 PATH。"
        )
    return path


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _short_error(stderr: str, fallback: str = "音频处理失败") -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return fallback
    # FFmpeg's final error line is normally the useful one.
    useful = lines[-1]
    if len(useful) > 500:
        useful = useful[:497] + "..."
    return useful


# ---------------------------------------------------------------------------
# Metadata and waveforms

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_RE = re.compile(
    r"Audio:\s*([^,\s]+)(?:[^,]*),\s*(\d+)\s*Hz,\s*([^,]+)"
)


def probe_audio(path: str | os.PathLike) -> AudioInfo:
    """Read duration and stream information without decoding the file."""
    target = Path(path)
    if not target.is_file():
        raise AudioError(f"音频文件不存在：{target}")
    cmd = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(target),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **_popen_kwargs(),
    )
    stderr = proc.stderr.decode("utf-8", "replace")
    match = _DURATION_RE.search(stderr)
    if not match:
        raise AudioError("无法读取音频时长，可能不是受支持的音频文件。")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    codec = ""
    sample_rate = 0
    channels = 0
    audio_match = _AUDIO_RE.search(stderr)
    if audio_match:
        codec = audio_match.group(1)
        sample_rate = int(audio_match.group(2))
        channel_text = audio_match.group(3).strip().lower()
        channels = {
            "mono": 1,
            "stereo": 2,
            "2 channels": 2,
            "1 channel": 1,
        }.get(channel_text, 2)
    return AudioInfo(
        duration=max(0.0, duration),
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
    )


def load_waveform(
    path: str | os.PathLike,
    buckets: int = 1100,
    sample_rate: int = 2500,
) -> Waveform:
    """Decode a compact mono track and return normalised peak buckets."""
    target = Path(path)
    if not target.is_file():
        raise AudioError(f"音频文件不存在：{target}")
    buckets = max(80, int(buckets))
    cmd = [
        str(ffmpeg_path()),
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(target),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_popen_kwargs(),
    )
    if proc.returncode != 0 and not proc.stdout:
        raise AudioError(_short_error(proc.stderr.decode("utf-8", "replace")))
    raw = proc.stdout
    if not raw:
        raise AudioError("音频波形为空，无法读取。")
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    total = len(samples)
    if total == 0:
        raise AudioError("音频波形为空，无法读取。")
    duration = total / float(sample_rate)
    step = max(1, total // buckets)
    peaks: list[float] = []
    absolute = 0
    for start in range(0, total, step):
        chunk = samples[start : start + step]
        if chunk:
            peak = max(max(chunk), -min(chunk)) / 32768.0
            peaks.append(peak)
            absolute = max(absolute, peak)
        if len(peaks) >= buckets:
            break
    if not peaks:
        peaks = [0.0]
    if absolute < 0.0008:
        peaks = [0.0 for _ in peaks]
    else:
        # The 99th percentile avoids one stray transient flattening the rest.
        ordered = sorted(peaks)
        reference = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
        reference = max(reference, absolute * 0.45, 1e-6)
        peaks = [min(1.0, (value / reference) ** 0.72) for value in peaks]
    return Waveform(duration=duration, peaks=tuple(peaks))


def format_duration(seconds: float, precise: bool = False) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if precise:
        minutes = int(seconds // 60)
        remain = seconds - minutes * 60
        return f"{minutes:02d}:{remain:06.3f}"
    total = int(round(seconds))
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration_zh(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分 {secs} 秒"
    return f"{minutes} 分 {secs:02d} 秒"


def parse_time(text: str) -> float:
    """Parse 1:23.5, 00:01:23.500, 83.5 or 1m23s into seconds."""
    cleaned = str(text).strip().replace("：", ":").replace("，", ".")
    if not cleaned:
        raise ValueError("时间不能为空")
    cleaned = cleaned.lower().replace("秒", "s").replace("分", "m")
    if "m" in cleaned or "s" in cleaned:
        match = re.fullmatch(
            r"(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?", cleaned
        )
        if match and any(match.groups()):
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2) or 0)
            return minutes * 60 + seconds
        raise ValueError("时间格式应为 01:23.500")
    if ":" in cleaned:
        parts = cleaned.split(":")
        if len(parts) > 3:
            raise ValueError("时间格式应为 01:23.500")
        values: list[float] = []
        for part in parts:
            if part == "":
                raise ValueError("时间格式应为 01:23.500")
            values.append(float(part))
        total = 0.0
        for value in values:
            total = total * 60 + value
        return total
    return float(cleaned)


def natural_sort_key(path: str | os.PathLike) -> list:
    name = Path(path).name.casefold()
    return [
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", name)
    ]


def scan_audio_files(folder: str | os.PathLike, recursive: bool = False) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        return []
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [
        item
        for item in iterator
        if item.is_file()
        and item.suffix.lower() in AUDIO_EXTENSIONS
        and not any(part.startswith(".") for part in item.relative_to(root).parts)
        and item.name.lower() != "thumbs.db"
    ]
    return sorted(files, key=natural_sort_key)


# ---------------------------------------------------------------------------
# Preview rendering


def render_preview(
    source: str | os.PathLike,
    destination: str | os.PathLike,
    start: float | None = None,
    end: float | None = None,
    volume: float = 1.0,
) -> Path:
    """Render a short, seek-friendly WAV clip for winsound playback."""
    source_path = Path(source)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source_path.is_file():
        raise AudioError(f"音频文件不存在：{source_path.name}")
    cmd = [str(ffmpeg_path()), "-y", "-v", "error", "-nostdin"]
    if start is not None and start > 0.001:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(source_path)]
    if start is not None and end is not None and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    elif end is not None:
        cmd += ["-t", f"{max(0.05, end):.3f}"]
    filters = []
    if abs(volume - 1.0) > 0.001:
        filters.append(f"volume={max(0.0, volume):.3f}")
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += [
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **_popen_kwargs(),
    )
    if proc.returncode != 0 or not target.is_file():
        raise AudioError(
            _short_error(
                proc.stderr.decode("utf-8", "replace"),
                "无法生成试听音频。",
            )
        )
    return target


# ---------------------------------------------------------------------------
# Export plan and execution


def _filter_number(value: float) -> str:
    return f"{max(0.0, float(value)):.3f}".rstrip("0").rstrip(".") or "0"


def build_export_plan(
    tracks: Sequence[ClipSpec],
    transition: TransitionSpec | None,
    output_path: str | os.PathLike,
    fmt: str = "mp3",
    bitrate: str = "320k",
    title: str = "随舞混剪",
    normalize: bool = False,
) -> ExportPlan:
    clips = [clip for clip in tracks if clip.duration > 0.05]
    if not clips:
        raise AudioError("没有可导出的选段。")
    fmt = fmt.lower()
    if fmt not in FORMAT_LABELS:
        raise AudioError(f"不支持的导出格式：{fmt}")

    boundaries = 0
    if transition and transition.insert_between:
        boundaries += max(0, len(clips) - 1)
    if transition and transition.at_end and len(clips) > 0:
        boundaries += 1
    if boundaries and (not transition or not transition.path.is_file()):
        raise AudioError("转场音频不存在，请重新选择默认转场。")

    inputs: list[str] = []
    filters: list[str] = []
    concat_parts: list[str] = []
    total = 0.0

    for index, clip in enumerate(clips):
        inputs += ["-i", str(clip.path)]
        duration = clip.duration
        total += duration
        fade = min(0.06, duration / 4)
        fade_out_at = max(0.0, duration - fade)
        chain = (
            f"[{index}:a]"
            f"atrim=start={_filter_number(clip.start)}:end={_filter_number(clip.end)},"
            "asetpts=PTS-STARTPTS,"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        )
        if fade > 0.005:
            chain += (
                f",afade=t=in:st=0:d={_filter_number(fade)}"
                f",afade=t=out:st={_filter_number(fade_out_at)}"
                f":d={_filter_number(fade)}"
            )
        chain += f"[s{index}]"
        filters.append(chain)

    transition_count = 0
    transition_clip = 0.0
    if boundaries and transition:
        transition_clip = transition.clip_length
        transition_count = boundaries
        total += transition_clip * boundaries
        trans_input = len(clips)
        inputs += ["-i", str(transition.path)]
        labels = [f"[tw{i}]" for i in range(boundaries)]
        filters.append(f"[{trans_input}:a]asplit={boundaries}" + "".join(labels))
        volume = max(0.0, min(1.5, transition.volume))
        fade = min(max(0.0, transition.fade), transition_clip / 3)
        fade_out_at = max(0.0, transition_clip - fade)
        for number, label in enumerate(labels):
            chain = (
                f"{label}"
                f"atrim=start=0:end={_filter_number(transition_clip)},"
                "asetpts=PTS-STARTPTS,"
                "aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            )
            if abs(volume - 1.0) > 0.001:
                chain += f",volume={_filter_number(volume)}"
            if fade > 0.005:
                chain += (
                    f",afade=t=in:st=0:d={_filter_number(fade)}"
                    f",afade=t=out:st={_filter_number(fade_out_at)}"
                    f":d={_filter_number(fade)}"
                )
            chain += f"[tr{number}]"
            filters.append(chain)

        tr_index = 0
        for index in range(len(clips)):
            concat_parts.append(f"[s{index}]")
            if index < len(clips) - 1 and transition.insert_between:
                concat_parts.append(f"[tr{tr_index}]")
                tr_index += 1
        if transition.at_end and tr_index < boundaries:
            concat_parts.append(f"[tr{tr_index}]")

    if not concat_parts:
        concat_parts = [f"[s{index}]" for index in range(len(clips))]

    if len(concat_parts) == 1:
        filters.append(f"{concat_parts[0]}anull[out]")
    else:
        filters.append(
            "".join(concat_parts)
            + f"concat=n={len(concat_parts)}:v=0:a=1[out]"
        )

    if normalize:
        filters.append(
            f"[out]loudnorm=I={LOUDNESS_TARGET:g}:TP={LOUDNESS_PEAK:g}:"
            f"LRA={LOUDNESS_RANGE:g}[normalized]"
        )

    plan = ExportPlan(
        tracks=clips,
        transition=transition,
        output_path=Path(output_path),
        fmt=fmt,
        bitrate=bitrate,
        title=title,
        filter_script=";\n".join(filters) + "\n",
        total_duration=total,
        transition_count=transition_count,
        normalize=normalize,
    )
    return plan


def export_command(plan: ExportPlan, filter_file: Path) -> list[str]:
    cmd = [
        str(ffmpeg_path()),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    for clip in plan.tracks:
        cmd += ["-i", str(clip.path)]
    if plan.transition_count and plan.transition:
        cmd += ["-i", str(plan.transition.path)]
    cmd += [
        "-filter_complex_script",
        str(filter_file),
        "-map",
        "[normalized]" if plan.normalize else "[out]",
        "-vn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-metadata",
        f"title={plan.title}",
        "-map_metadata",
        "-1",
    ]
    if plan.fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", plan.bitrate or "320k", "-id3v2_version", "3"]
    elif plan.fmt == "m4a":
        cmd += ["-c:a", "aac", "-b:a", plan.bitrate or "256k", "-movflags", "+faststart"]
    elif plan.fmt == "flac":
        cmd += ["-c:a", "flac", "-compression_level", "5"]
    else:
        cmd += ["-c:a", "pcm_s16le"]
    cmd.append(str(plan.output_path))
    return cmd


def run_export(
    plan: ExportPlan,
    progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    filter_file: Path | None = None,
) -> Path:
    """Execute an export plan, streaming progress in the range 0..1."""
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_filter = filter_file is None
    if filter_file is None:
        import uuid

        # Keep the filter script beside the output. This avoids system-temp
        # permission surprises and is removed as soon as FFmpeg exits.
        filter_file = plan.output_path.parent / f".suibu_filters_{os.getpid()}_{uuid.uuid4().hex}.txt"
        cleanup_filter = True
    filter_file.write_text(plan.filter_script, encoding="utf-8")
    cmd = export_command(plan, filter_file)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **_popen_kwargs(),
    )
    cancelled = False
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process.terminate()
                break
            if progress:
                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    if plan.total_duration > 0:
                        progress(max(0.0, min(0.995, seconds / plan.total_duration)))
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    finally:
        stderr = process.stderr.read() if process.stderr else ""
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if cleanup_filter:
            try:
                filter_file.unlink(missing_ok=True)
            except OSError:
                pass
    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        try:
            plan.output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise AudioCancelled("已取消导出。")
    if process.returncode != 0:
        raise AudioError(_short_error(stderr, "导出失败，请检查音频格式。"))
    if not plan.output_path.is_file() or plan.output_path.stat().st_size == 0:
        raise AudioError("导出没有生成有效文件。")
    if progress:
        progress(1.0)
    return plan.output_path


def make_unique_path(path: str | os.PathLike) -> Path:
    """Return path, adding (2), (3)... if the file already exists."""
    target = Path(path)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for number in range(2, 1000):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{stem} ({os.getpid()}){suffix}")
