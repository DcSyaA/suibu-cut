from __future__ import annotations

import shutil
import subprocess
import threading
import unittest
import uuid
from pathlib import Path

from svdj.audio import (
    AudioCancelled,
    ClipSpec,
    TransitionSpec,
    build_export_plan,
    find_ffmpeg,
    format_duration,
    load_waveform,
    parse_time,
    probe_audio,
    run_export,
)
from svdj.project import Project, TransitionSettings


ROOT = Path(__file__).resolve().parent.parent
FFMPEG = find_ffmpeg()
TEST_TMP = ROOT / ".testtmp"


def make_temp_dir(prefix: str) -> Path:
    TEST_TMP.mkdir(exist_ok=True)
    path = TEST_TMP / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir()
    return path


class AudioUnitTests(unittest.TestCase):
    def test_parse_time_formats(self):
        self.assertAlmostEqual(parse_time("83.5"), 83.5)
        self.assertAlmostEqual(parse_time("01:23.500"), 83.5)
        self.assertAlmostEqual(parse_time("1:23.5"), 83.5)
        self.assertAlmostEqual(parse_time("1m23.5s"), 83.5)
        with self.assertRaises(ValueError):
            parse_time("abc")

    def test_format_duration(self):
        self.assertEqual(format_duration(83.5), "01:24")
        self.assertEqual(format_duration(83.5, precise=True), "01:23.500")
        self.assertEqual(format_duration(3723), "1:02:03")

    def test_natural_sort(self):
        from svdj.audio import natural_sort_key

        names = ["10 song.mp3", "2 song.mp3", "1 song.mp3"]
        self.assertEqual(
            sorted(names, key=natural_sort_key),
            ["1 song.mp3", "2 song.mp3", "10 song.mp3"],
        )


@unittest.skipUnless(FFMPEG is not None, "ffmpeg is not available")
class AudioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = make_temp_dir("audio_")
        for name, frequency, duration in (
            ("a.mp3", 220, 4.0),
            ("b.mp3", 440, 3.0),
            ("transition.mp3", 180, 1.2),
        ):
            command = [
                str(FFMPEG),
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(cls.base / name),
            ]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_probe_and_waveform(self):
        info = probe_audio(self.base / "a.mp3")
        self.assertAlmostEqual(info.duration, 4.0, delta=0.2)
        self.assertEqual(info.sample_rate, 44100)
        waveform = load_waveform(self.base / "a.mp3", buckets=100)
        self.assertGreater(len(waveform.peaks), 50)
        self.assertGreater(max(waveform.peaks), 0.5)
        self.assertAlmostEqual(waveform.duration, 4.0, delta=0.2)

    def test_export_with_transitions(self):
        clip_a = ClipSpec(self.base / "a.mp3", 0.2, 1.8)
        clip_b = ClipSpec(self.base / "b.mp3", 0.1, 1.2)
        transition = TransitionSpec(
            self.base / "transition.mp3",
            source_duration=1.2,
            length=0.5,
            full=False,
            volume=0.8,
            fade=0.1,
            at_end=True,
        )
        output = self.base / "mix.wav"
        plan = build_export_plan([clip_a, clip_b], transition, output, fmt="wav")
        self.assertAlmostEqual(plan.total_duration, (1.6 + 1.1) + 1.0, places=2)
        self.assertEqual(plan.transition_count, 2)
        run_export(plan)
        self.assertTrue(output.is_file())
        mixed = probe_audio(output)
        self.assertAlmostEqual(mixed.duration, 3.7, delta=0.15)
        self.assertEqual(mixed.sample_rate, 48000)
        self.assertEqual(mixed.channels, 2)

    def test_export_without_transition(self):
        clip = ClipSpec(self.base / "a.mp3", 1.0, 2.0)
        output = self.base / "single.flac"
        plan = build_export_plan([clip], None, output, fmt="flac")
        self.assertEqual(plan.transition_count, 0)
        run_export(plan)
        self.assertAlmostEqual(probe_audio(output).duration, 1.0, delta=0.1)

    def test_export_cancelled_removes_partial_file(self):
        clip = ClipSpec(self.base / "a.mp3", 0.0, 4.0)
        output = self.base / "cancelled.wav"
        plan = build_export_plan([clip], None, output, fmt="wav")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(AudioCancelled):
            run_export(plan, cancel_event=cancel)
        self.assertFalse(output.exists())

    def test_export_with_loudness_normalization(self):
        clip_a = ClipSpec(self.base / "a.mp3", 0.0, 1.0)
        clip_b = ClipSpec(self.base / "b.mp3", 0.0, 1.0)
        output = self.base / "normalized.wav"
        plan = build_export_plan(
            [clip_a, clip_b], None, output, fmt="wav", normalize=True
        )
        self.assertIn("loudnorm", plan.filter_script)
        self.assertTrue(plan.normalize)
        run_export(plan)
        mixed = probe_audio(output)
        self.assertAlmostEqual(mixed.duration, 2.0, delta=0.15)
        self.assertEqual(mixed.sample_rate, 48000)
        self.assertEqual(mixed.channels, 2)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.base = make_temp_dir("project_")
        for name in ("02 song.mp3", "10 song.mp3", "01 song.mp3"):
            (self.base / name).write_bytes(b"RIFF")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_scan_natural_order(self):
        project = Project.open_folder(self.base)
        self.assertEqual(
            [track.name for track in project.tracks],
            ["01 song.mp3", "02 song.mp3", "10 song.mp3"],
        )

    def test_remove_is_persistent(self):
        project = Project.open_folder(self.base)
        removed = project.tracks[1]
        project.remove_track(removed)
        project.save()
        reloaded = Project.open_folder(self.base)
        self.assertNotIn(removed.name, [track.name for track in reloaded.tracks])
        self.assertTrue(reloaded.excluded_paths)

    def test_manual_reorder_persists(self):
        project = Project.open_folder(self.base)
        project.move_track(0, 2)
        project.save()
        reloaded = Project.open_folder(self.base)
        self.assertEqual(reloaded.tracks[-1].name, "01 song.mp3")

    def test_sort_by_duration(self):
        project = Project.open_folder(self.base)
        for duration, track in zip((9.0, 2.0, 5.0), project.tracks):
            track.duration = duration
            track.start = 0.0
            track.end = duration
        project.sort_tracks("duration")
        self.assertEqual([track.duration for track in project.tracks], [2.0, 5.0, 9.0])

    def test_output_normalize_persists(self):
        project = Project.open_folder(self.base)
        project.output.normalize = True
        project.save()
        reloaded = Project.open_folder(self.base)
        self.assertTrue(reloaded.output.normalize)

    def test_transition_boundaries_include_end_only(self):
        project = Project.open_folder(self.base)
        transition_path = self.base / "transition.mp3"
        transition_path.write_bytes(b"RIFF")
        project.transition = TransitionSettings(
            path=transition_path,
            source_duration=1.0,
            length=0.5,
            insert_between=False,
            at_end=True,
        )
        self.assertEqual(project.transition_boundary_count(), 1)
        project.transition.at_end = False
        self.assertEqual(project.transition_boundary_count(), 0)
        project.transition.insert_between = True
        project.transition.at_end = True
        self.assertEqual(project.transition_boundary_count(), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
