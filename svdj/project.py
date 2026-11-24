"""Project model and JSON persistence for 随舞剪辑台."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import AUDIO_EXTENSIONS, natural_sort_key, scan_audio_files


PROJECT_FILENAME = "随舞项目.json"
OUTPUT_FOLDER = "输出"
APP_NAME = "随舞剪辑台"
PROJECT_VERSION = 1


class ProjectError(RuntimeError):
    pass


@dataclass
class Track:
    path: Path
    duration: float = 0.0
    start: float = 0.0
    end: float = 0.0
    enabled: bool = True
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    probe_error: str = ""
    # Path as it was stored, useful for relative project paths.
    stored_path: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def clip_duration(self) -> float:
        if self.end <= 0 and self.duration > 0:
            return self.duration
        return max(0.0, self.end - self.start)

    @property
    def is_missing(self) -> bool:
        try:
            return not self.path.is_file()
        except OSError:
            return True

    def normalise(self) -> None:
        if self.duration <= 0:
            self.start = max(0.0, self.start)
            self.end = 0.0
            return
        self.start = min(max(0.0, self.start), max(0.0, self.duration - 0.05))
        if self.end <= 0 or self.end > self.duration:
            self.end = self.duration
        self.end = min(max(self.start + 0.05, self.end), self.duration)

    def metadata_text(self) -> str:
        bits = []
        if self.codec:
            bits.append(self.codec.upper())
        if self.sample_rate:
            bits.append(f"{self.sample_rate / 1000:g} kHz")
        if self.channels:
            bits.append("立体声" if self.channels >= 2 else "单声道")
        return " · ".join(bits)


@dataclass
class TransitionSettings:
    path: Path | None = None
    source_duration: float = 0.0
    length: float = 2.5
    full: bool = False
    volume: float = 0.85
    fade: float = 0.18
    insert_between: bool = True
    at_end: bool = False

    @property
    def clip_length(self) -> float:
        if self.path is None:
            return 0.0
        if self.source_duration <= 0:
            return self.length
        return max(0.05, self.source_duration if self.full else min(self.length, self.source_duration))

    def validate(self) -> None:
        self.length = min(30.0, max(0.2, float(self.length)))
        self.volume = min(1.5, max(0.0, float(self.volume)))
        self.fade = min(5.0, max(0.0, float(self.fade)))


@dataclass
class OutputSettings:
    fmt: str = "mp3"
    bitrate: str = "320k"
    filename: str = ""
    normalize: bool = False


@dataclass
class Project:
    folder: Path
    tracks: list[Track] = field(default_factory=list)
    transition: TransitionSettings = field(default_factory=TransitionSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    warnings: list[str] = field(default_factory=list)
    excluded_paths: list[Path] = field(default_factory=list)
    dirty: bool = False

    # ------------------------------------------------------------------
    # Construction

    @classmethod
    def open_folder(cls, folder: str | os.PathLike, add_new_files: bool = True) -> "Project":
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ProjectError("所选文件夹不存在。")
        project_file = root / PROJECT_FILENAME
        project: Project | None = None
        warnings: list[str] = []
        if project_file.is_file():
            try:
                project = cls._from_file(project_file)
            except Exception as exc:
                warnings.append(f"项目文件读取失败，已按文件夹重新建立：{exc}")
        if project is None:
            project = cls(folder=root)
            project._import_folder_files()
        elif add_new_files:
            project._add_new_folder_files()
        if project.transition.path:
            transition_key = project._path_key(project.transition.path)
            project.tracks = [
                track
                for track in project.tracks
                if project._path_key(track.path) != transition_key
            ]
        project.warnings = warnings + project.warnings
        project.ensure_output_name()
        return project

    def _import_folder_files(self) -> None:
        for path in scan_audio_files(self.folder):
            self.tracks.append(Track(path=path, stored_path=self._stored(path)))

    def _add_new_folder_files(self) -> None:
        known = {self._path_key(track.path) for track in self.tracks}
        transition_key = (
            self._path_key(self.transition.path) if self.transition.path else ""
        )
        excluded = {self._path_key(path) for path in self.excluded_paths}
        added = 0
        for path in scan_audio_files(self.folder):
            key = self._path_key(path)
            if key in known or key in excluded or (transition_key and key == transition_key):
                continue
            self.tracks.append(Track(path=path, stored_path=self._stored(path)))
            known.add(key)
            added += 1
        if added:
            self.dirty = True
            self.warnings.append(f"发现 {added} 首新歌，已添加到歌单末尾。")

    # ------------------------------------------------------------------
    # Persistence

    @classmethod
    def _from_file(cls, project_file: Path) -> "Project":
        data = json.loads(project_file.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("项目内容格式不正确")
        folder = project_file.parent.resolve()
        transition_data = data.get("transition") or {}
        output_data = data.get("output") or {}
        project = cls(
            folder=folder,
            transition=TransitionSettings(
                path=cls._resolve(folder, transition_data.get("path")),
                source_duration=float(transition_data.get("duration") or 0),
                length=float(transition_data.get("length") or 2.5),
                full=bool(transition_data.get("full", False)),
                volume=float(transition_data.get("volume", 0.85)),
                fade=float(transition_data.get("fade", 0.18)),
                insert_between=bool(transition_data.get("insert_between", True)),
                at_end=bool(transition_data.get("at_end", False)),
            ),
            output=OutputSettings(
                fmt=str(output_data.get("format") or "mp3").lower(),
                bitrate=str(output_data.get("bitrate") or "320k"),
                filename=str(output_data.get("filename") or ""),
                normalize=bool(output_data.get("normalize", False)),
            ),
        )
        raw_excluded = data.get("excluded")
        if isinstance(raw_excluded, list):
            for value in raw_excluded:
                resolved = cls._resolve(folder, str(value)) if value else None
                if resolved is not None:
                    project.excluded_paths.append(resolved)
        raw_tracks = data.get("tracks")
        if not isinstance(raw_tracks, list):
            raw_tracks = []
        for item in raw_tracks:
            if not isinstance(item, dict):
                continue
            path_value = item.get("path")
            if not path_value:
                continue
            path = cls._resolve(folder, path_value)
            if path is None:
                continue
            track = Track(
                path=path,
                duration=max(0.0, float(item.get("duration") or 0)),
                start=max(0.0, float(item.get("start") or 0)),
                end=max(0.0, float(item.get("end") or 0)),
                enabled=bool(item.get("enabled", True)),
                codec=str(item.get("codec") or ""),
                sample_rate=int(item.get("sample_rate") or 0),
                channels=int(item.get("channels") or 0),
                stored_path=str(path_value),
            )
            track.normalise()
            project.tracks.append(track)
        project.transition.validate()
        if project.output.fmt not in {"mp3", "wav", "m4a", "flac"}:
            project.output.fmt = "mp3"
        return project

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": APP_NAME,
            "version": PROJECT_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "tracks": [
                {
                    "path": self._stored(track.path),
                    "duration": round(track.duration, 3),
                    "start": round(track.start, 3),
                    "end": round(track.end, 3),
                    "enabled": bool(track.enabled),
                    "codec": track.codec,
                    "sample_rate": track.sample_rate,
                    "channels": track.channels,
                }
                for track in self.tracks
            ],
            "excluded": [self._stored(path) for path in self.excluded_paths],
            "transition": {
                "path": self._stored(self.transition.path) if self.transition.path else "",
                "duration": round(self.transition.source_duration, 3),
                "length": round(self.transition.length, 3),
                "full": bool(self.transition.full),
                "volume": round(self.transition.volume, 3),
                "fade": round(self.transition.fade, 3),
                "insert_between": bool(self.transition.insert_between),
                "at_end": bool(self.transition.at_end),
            },
            "output": {
                "format": self.output.fmt,
                "bitrate": self.output.bitrate,
                "filename": self.output.filename,
                "normalize": bool(self.output.normalize),
            },
        }

    def save(self) -> Path:
        target = self.folder / PROJECT_FILENAME
        temporary: Path | None = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)
        except OSError as exc:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            # A direct write is better than losing the project on unusual
            # filesystems where replace is not permitted.
            try:
                target.write_text(
                    json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                raise ProjectError(f"无法保存项目：{exc}") from exc
        self.dirty = False
        return target

    # ------------------------------------------------------------------
    # Utility

    def _stored(self, path: Path | None) -> str:
        if path is None:
            return ""
        path = Path(path)
        try:
            return path.resolve().relative_to(self.folder).as_posix()
        except (ValueError, OSError):
            return str(path)

    @staticmethod
    def _resolve(folder: Path, value: str | None) -> Path | None:
        if value is None or str(value).strip() == "":
            return None
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = folder / path
        return path.resolve()

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return os.path.normcase(str(path.resolve())).casefold()
        except OSError:
            return os.path.normcase(str(path)).casefold()

    def add_paths(self, paths: list[str | os.PathLike]) -> list[Track]:
        existing = {self._path_key(track.path) for track in self.tracks}
        result: list[Track] = []
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                path = path.resolve()
            except OSError:
                pass
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            key = self._path_key(path)
            if key in existing:
                continue
            self.excluded_paths = [
                item for item in self.excluded_paths if self._path_key(item) != key
            ]
            track = Track(path=path, stored_path=self._stored(path))
            self.tracks.append(track)
            existing.add(key)
            result.append(track)
        if result:
            self.dirty = True
        return result

    def remove_track(self, track: Track) -> None:
        if track in self.tracks:
            self.tracks.remove(track)
            key = self._path_key(track.path)
            if not any(self._path_key(item) == key for item in self.excluded_paths):
                self.excluded_paths.append(track.path)
            self.dirty = True

    def move_track(self, index: int, new_index: int) -> bool:
        if index < 0 or index >= len(self.tracks):
            return False
        new_index = max(0, min(len(self.tracks) - 1, new_index))
        if index == new_index:
            return False
        track = self.tracks.pop(index)
        self.tracks.insert(new_index, track)
        self.dirty = True
        return True

    def sort_tracks(self, key: str, reverse: bool = False) -> None:
        if key == "name":
            self.tracks.sort(key=lambda track: natural_sort_key(track.name), reverse=reverse)
        elif key == "duration":
            self.tracks.sort(key=lambda track: track.duration, reverse=reverse)
        elif key == "clip":
            self.tracks.sort(key=lambda track: track.clip_duration, reverse=reverse)
        self.dirty = True

    def enabled_tracks(self) -> list[Track]:
        return [track for track in self.tracks if track.enabled and not track.is_missing]

    def transition_boundary_count(self) -> int:
        if not self.transition.path:
            return 0
        tracks = self.enabled_tracks()
        count = max(0, len(tracks) - 1) if self.transition.insert_between else 0
        if self.transition.at_end and tracks:
            count += 1
        return count

    def total_duration(self) -> float:
        tracks = self.enabled_tracks()
        total = sum(track.clip_duration for track in tracks)
        if self.transition.path:
            if self.transition.insert_between:
                total += self.transition.clip_length * max(0, len(tracks) - 1)
            if self.transition.at_end and tracks:
                total += self.transition.clip_length
        return total

    def ensure_output_name(self) -> None:
        if not self.output.filename:
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            self.output.filename = f"随舞混剪_{stamp}.{self.output.fmt}"

    def output_path(self) -> Path:
        name = self.output.filename.strip() or f"随舞混剪.{self.output.fmt}"
        # Never let a typed filename escape the project output folder.
        name = Path(name).name
        if not Path(name).suffix:
            name = f"{name}.{self.output.fmt}"
        return self.folder / OUTPUT_FOLDER / name

    def update_output_extension(self) -> None:
        current = Path(self.output.filename)
        if current.suffix.lower() != f".{self.output.fmt}":
            stem = current.stem or "随舞混剪"
            self.output.filename = f"{stem}.{self.output.fmt}"


def create_demo_folder(folder: str | os.PathLike) -> Path:
    """Create a small demo project used for screenshots and quick trials."""
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    return root
