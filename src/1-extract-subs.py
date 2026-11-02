# Extracts the tracks of each video into text subtitles and image subtitles.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.progress import TaskProgressColumn, TimeElapsedColumn

from shared.console import Batch, Color, log_action, progress_bar
from shared.files import SUBS_FOLDER, find_files, parse_movie_dir


# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class SubtitleFormat:
    extension: str
    codec: str
    muxer: str


FORMAT_SRT = SubtitleFormat(extension="srt", codec="srt", muxer="srt")
FORMAT_SUP = SubtitleFormat(extension="sup", codec="copy", muxer="sup")
FORMAT_MKS = SubtitleFormat(extension="mks", codec="copy", muxer="matroska")
FORMATS = {
    "subrip": FORMAT_SRT,
    "ass": FORMAT_SRT,
    "ssa": FORMAT_SRT,
    "mov_text": FORMAT_SRT,
    "webvtt": FORMAT_SRT,
    "text": FORMAT_SRT,
    "hdmv_pgs_subtitle": FORMAT_SUP,
    "dvd_subtitle": FORMAT_MKS,
}

VIDEO_EXTENSIONS = (".mkv", ".mp4")


@dataclass(frozen=True)
class SubtitleExtraction:
    index: int
    format: SubtitleFormat
    description: str
    target_file: Path

    @property
    def target_file_part(self) -> Path:
        return self.target_file.with_name(f"{self.target_file.name}.part")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    videos = find_files("Extracts the tracks of each video into text subtitles and image subtitles.", extensions_file=VIDEO_EXTENSIONS, extensions_folder=VIDEO_EXTENSIONS)
    batch = Batch()

    for video in videos:
        batch.log_movie(parse_movie_dir(video))

        subs_dir = video.parent / SUBS_FOLDER
        subs_pending: list[SubtitleExtraction] = []
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
                log_action("empty", Color.YELLOW, f"{'':<4} {'no streams':<18} {'':<4} {'':<10} {video.name}")
                continue

            # 2. select streams to extract, skipping ones already extracted (and possibly renamed with a language suffix)
            subs_existing = [path.name for path in subs_dir.iterdir() if path.is_file()] if subs_dir.is_dir() else []
            for sub_stream in subs_streams:
                disposition = sub_stream.get("disposition", {})
                flags = ",".join(flag for flag, enabled in (("forced", disposition.get("forced")), ("sdh", disposition.get("hearing_impaired"))) if enabled)
                codec = sub_stream.get("codec_name", "")
                language = sub_stream.get("tags", {}).get("language", "")
                description = f"{'#' + str(sub_stream['index']):<4} {codec:<18} {language:<4} {flags:<10} {video.name}"

                # skip unsupported streams
                sub_format = FORMATS.get(codec)
                if sub_format is None:
                    log_action("skip", Color.YELLOW, description)
                    continue
                # skip already extracted stream
                target_prefix = f"{video.stem}.embed.{sub_stream['index']}."
                if any(name.startswith(target_prefix) and name.endswith(f".{sub_format.extension}") for name in subs_existing):
                    log_action("exists", Color.BLUE, description)
                    continue
                log_action("extract", Color.GREEN, description)
                subs_pending.append(SubtitleExtraction(
                    index=sub_stream["index"],
                    format=sub_format,
                    description=description,
                    target_file=subs_dir / f"{target_prefix}{sub_format.extension}",
                ))
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
                    log_action("discard", Color.YELLOW, f"{sub.description} (empty)")
                    continue
                sub.target_file_part.replace(sub.target_file)
        except Exception as error:
            batch.fail(video, error)
        finally:
            for sub in subs_pending:
                sub.target_file_part.unlink(missing_ok=True)

    batch.finish()
