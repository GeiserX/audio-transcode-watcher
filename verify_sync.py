#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_sync.py - Music Library Sync Verification Tool

Compares source FLAC folder with encoded output folders (ALAC, MP3, AAC) to find:
- Missing files (in source but not in destination)
- Extra files (in destination but not in source)
- Duration mismatches between source and encoded files

Usage:
    python verify_sync.py [--src /path/to/flac] [--alac /path/to/alac] \
                          [--mp3 /path/to/mp3] [--aac /path/to/aac] \
                          [--check-duration] [--fix-duration-threshold SECONDS]

Environment variables (defaults):
    SRC_DIR   = /music/flac
    DEST_ALAC = /music/alac
    DEST_MP3  = /music/mp3256
    DEST_AAC  = /music/aac256
"""

import os
import sys
import json
import argparse
import subprocess
import unicodedata
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

# Audio extensions considered lossless
LOSSLESS_EXT = {
    ".flac", ".alac", ".wav", ".ape", ".aiff",
    ".wv", ".tta", ".ogg", ".opus"
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
    except Exception as e:
        print(f"  Warning: Could not get duration for {filepath}: {e}", file=sys.stderr)
    return None


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
class SyncReport:
    """Report of sync verification results."""
    source_count: int = 0
    dest_count: int = 0
    missing_in_dest: list[str] = None
    extra_in_dest: list[str] = None
    duration_mismatches: list[dict] = None
    
    def __post_init__(self):
        if self.missing_in_dest is None:
            self.missing_in_dest = []
        if self.extra_in_dest is None:
            self.extra_in_dest = []
        if self.duration_mismatches is None:
            self.duration_mismatches = []


def verify_folder(
    src_files: dict[str, str],
    dest_dir: str,
    dest_ext: set[str],
    check_duration: bool = False,
    duration_threshold: float = 2.0
) -> SyncReport:
    """
    Verify a destination folder against source files.
    
    Args:
        src_files: Dict of stem -> filepath for source files
        dest_dir: Destination directory path
        dest_ext: Set of valid extensions in destination
        check_duration: Whether to compare durations
        duration_threshold: Max acceptable duration difference in seconds
    
    Returns:
        SyncReport with verification results
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
    
    # Check duration mismatches
    if check_duration:
        common = src_stems & dest_stems
        for stem in sorted(common):
            src_path = src_files[stem]
            dest_path = dest_files[stem]
            
            src_dur = get_duration(src_path)
            dest_dur = get_duration(dest_path)
            
            if src_dur is not None and dest_dur is not None:
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
    mismatch_count = len(report.duration_mismatches)
    
    if missing_count == 0 and extra_count == 0 and mismatch_count == 0:
        print("✅ All files synced correctly!")
        return
    
    if missing_count > 0:
        print(f"\n❌ Missing files ({missing_count}):")
        if verbose:
            for path in report.missing_in_dest[:50]:  # Limit output
                print(f"   - {Path(path).name}")
            if missing_count > 50:
                print(f"   ... and {missing_count - 50} more")
        else:
            print(f"   (use --verbose to see list)")
    
    if extra_count > 0:
        print(f"\n⚠️  Extra files in destination ({extra_count}):")
        if verbose:
            for path in report.extra_in_dest[:20]:
                print(f"   - {Path(path).name}")
            if extra_count > 20:
                print(f"   ... and {extra_count - 20} more")
    
    if mismatch_count > 0:
        print(f"\n🔴 Duration mismatches ({mismatch_count}):")
        for m in report.duration_mismatches:
            src_fmt = format_duration(m["source_duration"])
            dest_fmt = format_duration(m["dest_duration"])
            print(f"   - {m['stem']}")
            print(f"     Source: {src_fmt} | Dest: {dest_fmt} | Diff: {m['difference']:.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description="Verify music library sync between source and encoded folders"
    )
    parser.add_argument("--src", default=os.getenv("SRC_DIR", "/music/flac"),
                        help="Source FLAC directory")
    parser.add_argument("--alac", default=os.getenv("DEST_ALAC", "/music/alac"),
                        help="ALAC destination directory")
    parser.add_argument("--mp3", default=os.getenv("DEST_MP3", "/music/mp3256"),
                        help="MP3 destination directory")
    parser.add_argument("--aac", default=os.getenv("DEST_AAC", "/music/aac256"),
                        help="AAC destination directory")
    parser.add_argument("--check-duration", action="store_true",
                        help="Also check for duration mismatches (slower)")
    parser.add_argument("--duration-threshold", type=float, default=2.0,
                        help="Max acceptable duration difference in seconds (default: 2.0)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed file lists")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    
    args = parser.parse_args()
    
    print(f"🔍 Music Library Sync Verification")
    print(f"   Source: {args.src}")
    
    # List source files
    src_files = list_audio_files(args.src, LOSSLESS_EXT.union({".mp3"}))
    
    if not src_files:
        print(f"❌ No audio files found in source directory: {args.src}")
        sys.exit(1)
    
    print(f"   Found {len(src_files)} source files")
    
    results = {}
    
    # Verify each destination
    destinations = [
        ("ALAC", args.alac, {".m4a", ".mp3"}),  # .mp3 files are copied as-is
        ("MP3-256", args.mp3, {".mp3"}),
        ("AAC-256", args.aac, {".m4a"}),
    ]
    
    for name, dest_dir, dest_ext in destinations:
        if not os.path.isdir(dest_dir):
            print(f"\n⚠️  {name} directory does not exist: {dest_dir}")
            continue
        
        report = verify_folder(
            src_files, dest_dir, dest_ext,
            check_duration=args.check_duration,
            duration_threshold=args.duration_threshold
        )
        results[name] = report
        
        if args.json:
            continue
        
        print_report(name, report, verbose=args.verbose)
    
    if args.json:
        # Convert to JSON-serializable format
        json_results = {}
        for name, report in results.items():
            json_results[name] = asdict(report)
        print(json.dumps(json_results, indent=2))
    
    # Summary
    if not args.json:
        print(f"\n{'='*60}")
        print("📊 Summary")
        print(f"{'='*60}")
        total_missing = sum(len(r.missing_in_dest) for r in results.values())
        total_extra = sum(len(r.extra_in_dest) for r in results.values())
        total_mismatch = sum(len(r.duration_mismatches) for r in results.values())
        
        print(f"Total missing files:      {total_missing}")
        print(f"Total extra files:        {total_extra}")
        if args.check_duration:
            print(f"Total duration mismatches: {total_mismatch}")
        
        if total_missing > 0:
            print("\n💡 Tip: Run the audio-transcoder with FORCE_REENCODE=false to encode missing files")
        
        sys.exit(0 if total_missing == 0 and total_mismatch == 0 else 1)


if __name__ == "__main__":
    main()
