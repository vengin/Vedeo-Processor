# Video Compression Processor

A Python GUI application for video files batch processing, with compression and tempo (speed) change, to reduce file size. The application uses FFmpeg (and FFprobe) for video processing in multi-threading mode and supports multiple video formats.

![Audio Tempo Changer Screenshot](./docs/tempo.png)

## Features

- Video compression
- Batch processing with multi-threading support
- Change video files tempo (with preserving the pitch)
- Supports multiple video formats (MP4, MKV, AVI, WEBM, FLV, WMV)
- Dynamic progress tracking for individual files and overall progress, based on processed time feedback from FFMPEG
- Configurable file overwrite behavior (Skip/Overwrite/Rename)
- Settings persistence between sessions (saves its configuration in config file)

## Requirements

- Python 3.x
- FFmpeg and FFprobe executables
- Python packages:
  - tkinter (usually comes with Python)
  - configparser

## Installation

1. Ensure Python 3.x is installed on your system
2. Download and install FFmpeg (with FFprobe)
3. Download `vidoe_processor.py` and run it

## Configuration

The application saves its configuration in `video_processor_config.ini` file, which includes:
- FFmpeg path
- Last used input (source) and output (destination) directories
- Tempo value
- Number of processing threads
- Overwrite options

## Usage

1. Set the FFmpeg path (first time only)
2. Select source directory containing video files
3. Select destination directory for processed files
4. Adjust tempo value (0-2, where 1 is normal speed)
5. Choose number of processing threads (1-DFLT_N_THREADS_MAX)
6. Select file overwrite behavior:
   - Skip existing files
   - Overwrite existing files
   - Rename existing files
7. Click "Run" to start processing

## Processing Options

- **Tempo**: Value between 0 and 2
  - < 1: Slower playback
  - 1: Normal speed
  - > 1: Faster playback
- **Threads**: 1-DFLT_N_THREADS_MAX concurrent processing threads
- **Overwrite Options**:
  - Skip: Preserve existing files
  - Overwrite: Replace existing files
  - Rename: Add number suffix to new files

## FFMPEG parameters

The FFMPEG command used for **compression (without tempo)** is the following:

```
# Cmd example:
# ffmpeg.exe -i i.mp4 -filter:v setpts=0.66666667*PTS,scale=640:360 -filter:a atempo=1.5 -vf scale=640:360 -pix_fmt yuv420p -c:v libaom-av1 -b:v 70k -crf 30 -cpu-used 8 -row-mt 1 -g 240 -aq-mode 0 -c:a aac -b:a 80k o.mp4 -y -progress pipe:1 -nostats -hide_banner -loglevel error
ffmpeg_command = [
  str(self.ffmpeg_path.get()),
  # General options
  "-i", src_file_path,
  # Filter options
  "-vf", "scale=640:360",
  "-pix_fmt", "yuv420p",
  # Video options
  "-c:v", "libaom-av1",
  "-b:v", "70k",
  "-crf", "30",
  "-cpu-used", "8",
  "-row-mt", "1",
  "-g", "240",
  "-aq-mode", "0",
  # Audio options
  "-c:a", "aac",
  "-b:a", "80k",
  # Output options
  dst_file_path,
  "-y",  # Force overwrite output file
  # Progress reporting
  "-progress", "pipe:1", # Pipe progress to stdout
  "-nostats", # Disable default stats output
  # Logging options
  "-hide_banner",
  "-loglevel", "error",
]
```

When **Tempo** is used (Tempo != 1), additional parameters are added:

```
# If tempo is not 1, we need to adjust both video and audio streams
# For video files we need to use tempo value for audio stream and PTS=1/tempo for video
PTS = 1 / self.tempo.get() # PTS is 1/tempo
ffmpeg_tempo_params = [
  "-filter:v", f"setpts={PTS:.8f}*PTS,scale=640:360",
  "-filter:a", f"atempo={self.tempo.get()}",  # tempo audio filter
]
# Replace ["-vf", "scale=640:360"], use single combined video filter
# Cmd example:
# ffmpeg.exe -i i.mp4 -filter:v setpts=0.66666667*PTS,scale=640:360 -filter:a atempo=1.5 -vf scale=640:360 -pix_fmt yuv420p -c:v libaom-av1 -b:v 70k -crf 30 -cpu-used 8 -row-mt 1 -g 240 -aq-mode 0 -c:a aac -b:a 80k o.mp4 -y -progress pipe:1 -nostats -hide_banner -loglevel error
ffmpeg_command[3:5] = ffmpeg_tempo_params
```

## Logging

The application logs processing details and errors to `video_processor.log` file ('INFO' or 'DEBUG' modes).
