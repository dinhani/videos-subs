# Extracts the tracks of each video into text subtitles and image subtitles.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.progress import TaskProgressColumn, TimeElapsedColumn

from shared.console import Action, Batch, progress_bar
from shared.files import SUBS_FOLDER, SubtitleFile, SubtitleFormat, find_files, parse_movie_dir


# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class TrackFormat:
    format: SubtitleFormat
    codec: str
    muxer: str


TRACK_TEXT = TrackFormat(format=SubtitleFormat.TEXT, codec="srt", muxer="srt")
TRACK_PGS = TrackFormat(format=SubtitleFormat.PGS, codec="copy", muxer="sup")
TRACK_VOBSUB = TrackFormat(format=SubtitleFormat.VOBSUB, codec="copy", muxer="matroska")
TRACK_FORMATS = {
    "subrip": TRACK_TEXT,
    "ass": TRACK_TEXT,
    "ssa": TRACK_TEXT,
    "mov_text": TRACK_TEXT,
    "webvtt": TRACK_TEXT,
    "text": TRACK_TEXT,
    "hdmv_pgs_subtitle": TRACK_PGS,
    "dvd_subtitle": TRACK_VOBSUB,
}

VIDEO_EXTENSIONS = (".mkv", ".mp4")


@dataclass(frozen=True)
class SubtitleExtraction:
    index: int
    format: TrackFormat
    description: str
    target: SubtitleFile

    @property
    def target_file_part(self) -> Path:
        return self.target.path.with_name(f"{self.target.name}.part")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    videos = find_files(argparse.ArgumentParser(description="Extracts the tracks of each video into text subtitles and image subtitles."), extensions_file=VIDEO_EXTENSIONS, extensions_folder=VIDEO_EXTENSIONS)

    with Batch() as batch:
        for video in videos:
            batch.log_movie(parse_movie_dir(video))

            subs_pending: list[SubtitleExtraction] = []
            with batch.attempt(video):
                try:
                    # 1. parse subtitle streams
                    probe_run = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index,codec_name:stream_tags=language:stream_disposition=forced,hearing_impaired:format=duration", "-print_format", "json", str(video)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if probe_run.returncode != 0:
                        raise RuntimeError(f"ffprobe exited with {probe_run.returncode}: {' | '.join(probe_run.stderr.splitlines())}")
                    probe = json.loads(probe_run.stdout)
                    subs_streams: list[dict] = probe.get("streams", [])
                    duration = float(probe.get("format", {}).get("duration", 0))
                    if not subs_streams:
                        Action.EMPTY.log(f"{'':<4} {'no streams':<18} {'':<4} {'':<10} {video.name}")
                        continue

                    # 2. select streams to extract, skipping ones already extracted (and possibly renamed with a language suffix)
                    subs_dir = video.parent / SUBS_FOLDER
                    subs_existing = [sub for path in subs_dir.iterdir() if path.is_file() and (sub := SubtitleFile.find(path)) is not None] if subs_dir.is_dir() else []
                    for sub_stream in subs_streams:
                        disposition = sub_stream.get("disposition", {})
                        flags = ",".join(flag for flag, enabled in (("forced", disposition.get("forced")), ("sdh", disposition.get("hearing_impaired"))) if enabled)
                        codec = sub_stream.get("codec_name", "")
                        language = sub_stream.get("tags", {}).get("language", "")
                        description = f"{'#' + str(sub_stream['index']):<4} {codec:<18} {language:<4} {flags:<10} {video.name}"

                        # skip unsupported streams
                        track_format = TRACK_FORMATS.get(codec)
                        if track_format is None:
                            Action.UNKNOWN.log(description)
                            continue
                        # skip already extracted stream
                        target = SubtitleFile.from_track(video, sub_stream["index"], track_format.format)
                        if any(sub.track == target.track and sub.format == target.format for sub in subs_existing):
                            Action.EXISTS.log(description)
                            continue
                        Action.EXTRACT.log(description)
                        subs_pending.append(SubtitleExtraction(index=sub_stream["index"], format=track_format, description=description, target=target))
                    if not subs_pending:
                        continue

                    # 3. extract streams in a single read of the video, showing progress
                    subs_dir.mkdir(exist_ok=True)
                    ffmpeg_args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-progress", "pipe:1", "-i", str(video)]
                    for sub in subs_pending:
                        ffmpeg_args += ["-map", f"0:{sub.index}", "-c:s", sub.format.codec, "-f", sub.format.muxer, str(sub.target_file_part)]
                    with tempfile.TemporaryFile() as ffmpeg_errors:
                        with progress_bar(TaskProgressColumn(), TimeElapsedColumn()) as progress, subprocess.Popen(ffmpeg_args, stdout=subprocess.PIPE, stderr=ffmpeg_errors, text=True, encoding="utf-8", errors="replace") as ffmpeg:
                            task = progress.add_task("", total=duration or None)
                            for line in ffmpeg.stdout or []:
                                if not line.startswith("out_time_us="):
                                    continue
                                out_time_us = line.removeprefix("out_time_us=").strip()
                                if out_time_us.isdigit():
                                    progress.update(task, completed=min(duration, int(out_time_us) / 1e6))
                        if ffmpeg.returncode != 0:
                            ffmpeg_errors.seek(0)
                            raise RuntimeError(f"ffmpeg exited with {ffmpeg.returncode}: {' | '.join(ffmpeg_errors.read().decode('utf-8', errors='replace').splitlines())}")

                    # 4. rename to target files
                    for sub in subs_pending:
                        if sub.target_file_part.stat().st_size == 0:
                            Action.DISCARD.log(f"{sub.description} (empty)")
                            continue
                        sub.target_file_part.replace(sub.target.path)
                finally:
                    for sub in subs_pending:
                        sub.target_file_part.unlink(missing_ok=True)
