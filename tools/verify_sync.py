#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_sync.py - Music Library Sync Verification Tool

Compares source folder with encoded output folders to find:
- Missing files (in source but not in destination)
- Extra files (in destination but not in source)
- Duration mismatches between source and encoded files
- Metadata mismatches (title, artist, album, etc.)

Usage:
    # Using config file (recommended)
    python verify_sync.py --config /path/to/config.yaml
    
    # Using CLI arguments
    python verify_sync.py --src /path/to/flac \\
        --output alac:/path/to/alac:.m4a,.mp3 \\
        --output mp3:/path/to/mp3:.mp3
    
    # With duration and metadata checking
    python verify_sync.py --config config.yaml --check-duration --check-metadata
"""

import argparse
import json
import os
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Number of parallel workers for ffprobe calls
VERIFY_WORKERS = 8

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# Audio extensions considered as source files
SOURCE_EXTENSIONS = {
    ".flac", ".alac", ".wav", ".ape", ".aiff",
    ".wv", ".tta", ".ogg", ".opus", ".mp3"
}


def nfc(s: str) -> str:
    """Normalize string to NFC Unicode form."""
    return unicodedata.normalize("NFC", s)


def get_stem(filename: str) -> str:
    """Get normalized stem (filename without extension)."""
    return nfc(Path(filename).stem)


def get_duration(filepath: str) -> Optional[float]:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                filepath
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


# Key metadata tags to check (normalized to lowercase)
# All important tags - normalization handles container-specific differences
METADATA_TAGS = ["title", "artist", "album", "album_artist", "track", "date", "genre"]


def get_metadata(filepath: str) -> dict[str, str]:
    """Get audio metadata using ffprobe."""
    metadata = {}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format_tags",
                "-of", "json",
                filepath
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            tags = data.get("format", {}).get("tags", {})
            # Normalize tag names to lowercase
            for key, value in tags.items():
                normalized_key = key.lower().replace(" ", "_")
                if normalized_key in METADATA_TAGS or key.lower() in METADATA_TAGS:
                    metadata[normalized_key] = str(value).strip()
    except Exception:
        pass
    return metadata


def get_file_info(filepath: str) -> tuple[Optional[float], dict[str, str]]:
    """Get both duration and metadata in a single ffprobe call."""
    duration = None
    metadata = {}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration:format_tags",
                "-of", "json",
                filepath
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            
            # Get duration
            if "duration" in fmt:
                try:
                    duration = float(fmt["duration"])
                except (ValueError, TypeError):
                    pass
            
            # Get metadata
            tags = fmt.get("tags", {})
            for key, value in tags.items():
                normalized_key = key.lower().replace(" ", "_")
                if normalized_key in METADATA_TAGS or key.lower() in METADATA_TAGS:
                    metadata[normalized_key] = str(value).strip()
    except Exception:
        pass
    return duration, metadata


def normalize_track_number(value: str) -> str:
    """Normalize track number by removing leading zeros."""
    if not value:
        return ""
    # Handle "track/total" format (e.g., "07/12" -> "7/12")
    if "/" in value:
        parts = value.split("/")
        return "/".join(p.lstrip("0") or "0" for p in parts)
    # Simple track number
    return value.lstrip("0") or "0"


def normalize_date(value: str) -> str:
    """
    Normalize date to just the year for comparison.
    Handles: 2024, 2024-01, 2024-01-15, 2024-01-15;2025-02-20, etc.
    """
    if not value:
        return ""
    # Take just the first date if multiple separated by semicolon
    first_date = value.split(";")[0].strip()
    # Extract just the year (first 4 digits)
    if len(first_date) >= 4 and first_date[:4].isdigit():
        return first_date[:4]
    return value


def compare_metadata(
    src_meta: dict[str, str],
    dest_meta: dict[str, str],
) -> list[str]:
    """
    Compare metadata between source and destination.
    Uses normalization for track numbers and dates to handle container differences.
    
    Returns list of differences.
    """
    differences = []
    
    for tag in METADATA_TAGS:
        src_val = src_meta.get(tag, "")
        dest_val = dest_meta.get(tag, "")
        
        # Normalize for comparison (strip whitespace, handle empty)
        src_normalized = src_val.strip() if src_val else ""
        dest_normalized = dest_val.strip() if dest_val else ""
        
        # Special handling for track numbers - normalize leading zeros
        if tag == "track":
            src_normalized = normalize_track_number(src_normalized)
            dest_normalized = normalize_track_number(dest_normalized)
        
        # Special handling for dates - normalize to year only
        if tag == "date":
            src_normalized = normalize_date(src_normalized)
            dest_normalized = normalize_date(dest_normalized)
        
        # Skip if both are empty
        if not src_normalized and not dest_normalized:
            continue
        
        # Check for mismatch
        if src_normalized != dest_normalized:
            differences.append(f"{tag}: '{src_normalized}' -> '{dest_normalized}'")
    
    return differences


def list_audio_files(directory: str, extensions: set[str]) -> dict[str, str]:
    """
    List audio files in directory matching extensions.
    Returns dict mapping normalized stem -> full filepath.
    """
    files = {}
    if not os.path.isdir(directory):
        return files
    
    try:
        for entry in os.scandir(directory):
            if entry.is_file():
                ext = Path(entry.name).suffix.lower()
                if ext in extensions:
                    stem = get_stem(entry.name)
                    files[stem] = entry.path
    except Exception as e:
        print(f"Error scanning {directory}: {e}", file=sys.stderr)
    
    return files


@dataclass
class OutputDefinition:
    """Definition of an output to verify."""
    name: str
    path: str
    extensions: set[str]


@dataclass
class SyncReport:
    """Report of sync verification results."""
    source_count: int = 0
    dest_count: int = 0
    missing_in_dest: list[str] = field(default_factory=list)
    extra_in_dest: list[str] = field(default_factory=list)
    duration_mismatches: list[dict] = field(default_factory=list)
    metadata_mismatches: list[dict] = field(default_factory=list)


def verify_folder(
    src_files: dict[str, str],
    dest_dir: str,
    dest_ext: set[str],
    check_duration: bool = False,
    check_metadata: bool = False,
    duration_threshold: float = 2.0,
    sample_size: int = 0,
) -> SyncReport:
    """
    Verify a destination folder against source files.
    
    Args:
        src_files: Dict of stem -> filepath for source files
        dest_dir: Destination directory path
        dest_ext: Set of valid extensions in destination
        check_duration: Whether to check duration mismatches
        check_metadata: Whether to check metadata mismatches
        duration_threshold: Max acceptable duration difference
        sample_size: If > 0, only check this many files for duration/metadata
    """
    report = SyncReport()
    report.source_count = len(src_files)
    
    dest_files = list_audio_files(dest_dir, dest_ext)
    report.dest_count = len(dest_files)
    
    src_stems = set(src_files.keys())
    dest_stems = set(dest_files.keys())
    
    # Find missing files
    missing = src_stems - dest_stems
    report.missing_in_dest = sorted([src_files[stem] for stem in missing])
    
    # Find extra files
    extra = dest_stems - src_stems
    report.extra_in_dest = sorted([dest_files[stem] for stem in extra])
    
    # Get common files for detailed checks
    common = sorted(src_stems & dest_stems)
    
    # Limit sample size if specified
    if sample_size > 0 and len(common) > sample_size:
        import random
        common = random.sample(common, sample_size)
        common = sorted(common)
    
    # Check duration and metadata in parallel
    if check_duration or check_metadata:
        total = len(common)
        print(f"   Analyzing {total} files with {VERIFY_WORKERS} workers...", flush=True)
        
        # Prepare file pairs
        file_pairs = [(stem, src_files[stem], dest_files[stem]) for stem in common]
        
        def analyze_pair(args):
            stem, src_path, dest_path = args
            src_dur, src_meta = get_file_info(src_path)
            dest_dur, dest_meta = get_file_info(dest_path)
            return stem, src_path, dest_path, src_dur, dest_dur, src_meta, dest_meta
        
        completed = 0
        with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as executor:
            futures = {executor.submit(analyze_pair, pair): pair for pair in file_pairs}
            
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0 or completed == total:
                    print(f"   Progress: {completed}/{total}...", end="\r", flush=True)
                
                try:
                    stem, src_path, dest_path, src_dur, dest_dur, src_meta, dest_meta = future.result()
                    
                    # Check duration
                    if check_duration and src_dur is not None and dest_dur is not None:
                        diff = abs(src_dur - dest_dur)
                        if diff > duration_threshold:
                            report.duration_mismatches.append({
                                "stem": stem,
                                "source": src_path,
                                "source_duration": round(src_dur, 2),
                                "dest": dest_path,
                                "dest_duration": round(dest_dur, 2),
                                "difference": round(diff, 2)
                            })
                    
                    # Check metadata
                    if check_metadata:
                        differences = compare_metadata(src_meta, dest_meta)
                        if differences:
                            report.metadata_mismatches.append({
                                "stem": stem,
                                "source": src_path,
                                "dest": dest_path,
                                "differences": differences
                            })
                except Exception:
                    pass
        
        print(f"   Progress: {total}/{total} done.        ")
    
    return report


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def print_report(name: str, report: SyncReport, verbose: bool = True):
    """Print a sync verification report."""
    print(f"\n{'='*60}")
    print(f"📁 {name}")
    print(f"{'='*60}")
    print(f"Source files:      {report.source_count}")
    print(f"Destination files: {report.dest_count}")
    
    missing_count = len(report.missing_in_dest)
    extra_count = len(report.extra_in_dest)
    duration_mismatch_count = len(report.duration_mismatches)
    metadata_mismatch_count = len(report.metadata_mismatches)
    
    total_issues = missing_count + extra_count + duration_mismatch_count + metadata_mismatch_count
    
    if total_issues == 0:
        print("✅ All files synced correctly!")
        return
    
    if missing_count > 0:
        print(f"\n❌ Missing files ({missing_count}):")
        if verbose:
            for path in report.missing_in_dest[:50]:
                print(f"   - {Path(path).name}")
            if missing_count > 50:
                print(f"   ... and {missing_count - 50} more")
        else:
            print("   (use --verbose to see list)")
    
    if extra_count > 0:
        print(f"\n⚠️  Extra files in destination ({extra_count}):")
        if verbose:
            for path in report.extra_in_dest[:20]:
                print(f"   - {Path(path).name}")
            if extra_count > 20:
                print(f"   ... and {extra_count - 20} more")
    
    if duration_mismatch_count > 0:
        print(f"\n🔴 Duration mismatches ({duration_mismatch_count}):")
        for m in report.duration_mismatches:
            src_fmt = format_duration(m["source_duration"])
            dest_fmt = format_duration(m["dest_duration"])
            print(f"   - {m['stem']}")
            print(f"     Source: {src_fmt} | Dest: {dest_fmt} | Diff: {m['difference']:.1f}s")
    
    if metadata_mismatch_count > 0:
        print(f"\n🏷️  Metadata mismatches ({metadata_mismatch_count}):")
        shown = 0
        for m in report.metadata_mismatches:
            if shown >= 20 and not verbose:
                print(f"   ... and {metadata_mismatch_count - shown} more (use -v to see all)")
                break
            print(f"   - {m['stem']}")
            for diff in m["differences"][:5]:
                print(f"     {diff}")
            if len(m["differences"]) > 5:
                print(f"     ... and {len(m['differences']) - 5} more tags")
            shown += 1


def load_outputs_from_config(config_path: str) -> tuple[str, list[OutputDefinition]]:
    """Load source and outputs from a YAML config file."""
    if not HAS_YAML:
        raise ImportError("PyYAML is required to load config files. Install with: pip install pyyaml")
    
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    source_path = data.get("source", {}).get("path", "")
    
    outputs = []
    for output_data in data.get("outputs", []):
        codec = output_data.get("codec", "")
        
        # Determine extensions based on codec
        if codec == "alac":
            extensions = {".m4a", ".mp3"}  # ALAC folder may have copied MP3s
        elif codec == "mp3":
            extensions = {".mp3"}
        elif codec in ("aac",):
            extensions = {".m4a"}
        elif codec == "opus":
            extensions = {".opus"}
        elif codec == "flac":
            extensions = {".flac"}
        elif codec == "wav":
            extensions = {".wav"}
        else:
            extensions = {".m4a", ".mp3", ".opus", ".flac", ".wav"}
        
        outputs.append(OutputDefinition(
            name=output_data.get("name", codec),
            path=output_data.get("path", ""),
            extensions=extensions,
        ))
    
    return source_path, outputs


def parse_output_arg(output_str: str) -> OutputDefinition:
    """Parse an output argument in format name:path:extensions."""
    parts = output_str.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid output format: {output_str}. Expected name:path[:extensions]")
    
    name = parts[0]
    path = parts[1]
    extensions = {".m4a", ".mp3"}  # Default
    
    if len(parts) >= 3:
        extensions = set(ext.strip() for ext in parts[2].split(","))
    
    return OutputDefinition(name=name, path=path, extensions=extensions)


def main():
    parser = argparse.ArgumentParser(
        description="Verify music library sync between source and encoded folders"
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to YAML config file"
    )
    parser.add_argument(
        "--src",
        help="Source directory (if not using config)"
    )
    parser.add_argument(
        "--output", "-o",
        action="append",
        help="Output definition: name:path[:extensions] (can specify multiple)"
    )
    parser.add_argument(
        "--check-duration",
        action="store_true",
        help="Also check for duration mismatches (slower)"
    )
    parser.add_argument(
        "--check-metadata",
        action="store_true",
        help="Check all metadata (title, artist, album, album_artist, track, date, genre)"
    )
    parser.add_argument(
        "--duration-threshold",
        type=float,
        default=2.0,
        help="Max acceptable duration difference in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        help="Only check this many random files for duration/metadata (0 = all)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed file lists"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    source_path = None
    outputs = []
    
    if args.config:
        source_path, outputs = load_outputs_from_config(args.config)
    
    if args.src:
        source_path = args.src
    
    if args.output:
        outputs = [parse_output_arg(o) for o in args.output]
    
    if not source_path:
        parser.error("Source path is required. Use --config or --src")
    
    if not outputs:
        parser.error("At least one output is required. Use --config or --output")
    
    print("🔍 Music Library Sync Verification")
    print(f"   Source: {source_path}")
    
    # List source files
    src_files = list_audio_files(source_path, SOURCE_EXTENSIONS)
    
    if not src_files:
        print(f"❌ No audio files found in source directory: {source_path}")
        sys.exit(1)
    
    print(f"   Found {len(src_files)} source files")
    
    results = {}
    
    # Show what checks are enabled
    if args.check_duration or args.check_metadata:
        checks = []
        if args.check_duration:
            checks.append("duration")
        if args.check_metadata:
            checks.append("metadata")
        sample_info = f" (sampling {args.sample_size} files)" if args.sample_size > 0 else ""
        print(f"   Checking: {', '.join(checks)}{sample_info}")
    
    # Verify each output
    for output in outputs:
        if not os.path.isdir(output.path):
            print(f"\n⚠️  {output.name} directory does not exist: {output.path}")
            continue
        
        report = verify_folder(
            src_files,
            output.path,
            output.extensions,
            check_duration=args.check_duration,
            check_metadata=args.check_metadata,
            duration_threshold=args.duration_threshold,
            sample_size=args.sample_size,
        )
        results[output.name] = report
        
        if not args.json:
            print_report(output.name, report, verbose=args.verbose)
    
    if args.json:
        json_results = {}
        for name, report in results.items():
            json_results[name] = asdict(report)
        print(json.dumps(json_results, indent=2))
        return
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 Summary")
    print(f"{'='*60}")
    total_missing = sum(len(r.missing_in_dest) for r in results.values())
    total_extra = sum(len(r.extra_in_dest) for r in results.values())
    total_duration_mismatch = sum(len(r.duration_mismatches) for r in results.values())
    total_metadata_mismatch = sum(len(r.metadata_mismatches) for r in results.values())
    
    print(f"Total missing files:       {total_missing}")
    print(f"Total extra files:         {total_extra}")
    if args.check_duration:
        print(f"Total duration mismatches: {total_duration_mismatch}")
    if args.check_metadata:
        print(f"Total metadata mismatches: {total_metadata_mismatch}")
    
    if total_missing > 0:
        print("\n💡 Tip: Restart the audio-transcoder to encode missing files")
    
    total_issues = total_missing + total_duration_mismatch + total_metadata_mismatch
    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
