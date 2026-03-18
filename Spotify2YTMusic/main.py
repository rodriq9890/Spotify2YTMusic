#!/usr/bin/env python3
"""
Spotify → YouTube Music Playlist Migrator
"""

import json
import time
import sys
from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from ytmusicapi import YTMusic

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
from logger import log, log_success, log_warn, log_error, log_section


# ─── Spotify ──────────────────────────────────────────────────────────────────

def get_spotify_client() -> spotipy.Spotify:
    scope = "user-library-read playlist-read-private playlist-read-collaborative"
    auth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=scope,
        cache_path=".spotify_token_cache",
    )
    return spotipy.Spotify(auth_manager=auth)


def fetch_liked_songs(sp: spotipy.Spotify) -> list[dict]:
    tracks = []
    offset = 0
    while True:
        result = sp.current_user_saved_tracks(limit=50, offset=offset)
        items = result.get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track")
            if track:
                tracks.append(_parse_track(track))
        offset += 50
        if result.get("next") is None:
            break
    return tracks


def fetch_playlists(sp: spotipy.Spotify) -> list[dict]:
    playlists = []
    result = sp.current_user_playlists(limit=50)
    while result:
        for pl in result.get("items", []):
            if not pl:
                continue
            track_count = pl.get('tracks', {}).get('total', 0) if pl.get('tracks') else 0
            log(f"  Fetching: {pl['name']} ({track_count} tracks)")
            tracks = _fetch_playlist_tracks(sp, pl["id"])
            if tracks or pl.get("owner", {}).get("id") == sp.current_user()["id"]:
                playlists.append({
                    "id": pl["id"],
                    "name": pl["name"],
                    "description": pl.get("description", ""),
                    "tracks": tracks,
                })
        result = sp.next(result) if result.get("next") else None
    return playlists


def _fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    tracks = []
    try:
        result = sp.playlist_items(playlist_id, limit=100)
        while result:
            for item in result.get("items", []):
                track = item.get("track") if item else None
                if track and track.get("id"):
                    tracks.append(_parse_track(track))
            result = sp.next(result) if result.get("next") else None
    except Exception as e:
        log_warn(f"    Skipping playlist, could not fetch tracks: {e}")
    return tracks


def _parse_track(track: dict) -> dict:
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return {
        "name": track.get("name", ""),
        "artist": artists,
        "album": track.get("album", {}).get("name", ""),
    }


# ─── YouTube Music ────────────────────────────────────────────────────────────

def get_ytmusic_client() -> YTMusic:
    browser_file = Path("browser.json")
    if not browser_file.exists():
        log_error("browser.json not found. Run: ytmusicapi browser")
        sys.exit(1)
    return YTMusic("browser.json")


def search_track(yt: YTMusic, track: dict) -> Optional[str]:
    query = f"{track['artist']} {track['name']}"
    try:
        results = yt.search(query, filter="songs", limit=5)
        if not results:
            results = yt.search(query, filter="videos", limit=3)
        if results:
            return results[0].get("videoId")
    except Exception as e:
        log_warn(f"    Search failed for '{query}': {e}")
    return None


def create_yt_playlist(yt: YTMusic, name: str, description: str, video_ids: list[str]) -> Optional[str]:
    if not video_ids:
        log_warn(f"  No tracks found for '{name}', skipping.")
        return None
    try:
        playlist_id = yt.create_playlist(
            title=name,
            description=description or f"Imported from Spotify – {name}",
            privacy_status="PRIVATE",
            video_ids=video_ids,
        )
        return playlist_id
    except Exception as e:
        log_error(f"  Failed to create '{name}': {e}")
        return None


# ─── Migration ────────────────────────────────────────────────────────────────

def get_existing_yt_playlist(yt: YTMusic, name: str) -> tuple[Optional[str], set[str]]:
    """Check if a playlist already exists on YT Music, return its ID and existing video IDs."""
    try:
        playlists = yt.get_library_playlists(limit=100)
        for pl in playlists:
            if pl.get("title", "").lower() == name.lower():
                playlist_id = pl["playlistId"]
                log(f"  Found existing playlist '{name}', fetching tracks...")
                existing = yt.get_playlist(playlist_id, limit=1000)
                existing_ids = set()
                for track in existing.get("tracks", []):
                    vid = track.get("videoId")
                    if vid:
                        existing_ids.add(vid)
                log(f"  {len(existing_ids)} tracks already in playlist, skipping those.")
                return playlist_id, existing_ids
    except Exception as e:
        log_warn(f"  Could not check existing playlists: {e}")
    return None, set()

def migrate_playlist(yt: YTMusic, playlist: dict, delay: float = 0.3, batch_size: int = 100) -> dict:
    name = playlist["name"]
    tracks = playlist["tracks"]
    log(f"\n  Migrating: '{name}' ({len(tracks)} tracks)")

    # Check if playlist already exists and get existing tracks
    playlist_id, existing_ids = get_existing_yt_playlist(yt, name)
    if playlist_id:
        log(f"  Resuming existing playlist...")

    video_ids = []
    not_found = []
    skipped = 0

    for i, track in enumerate(tracks, 1):
        vid = search_track(yt, track)
        label = f"{track['artist']} – {track['name']}"

        if vid and vid in existing_ids:
            skipped += 1
            log(f"    [{i}/{len(tracks)}] ~ Skipping (already in playlist): {label}")
            continue

        if vid:
            video_ids.append(vid)
            log(f"    [{i}/{len(tracks)}] ✓ {label}")
        else:
            not_found.append(label)
            log_warn(f"    [{i}/{len(tracks)}] ✗ Not found: {label}")
        time.sleep(delay)

        # Every batch_size songs, save to YouTube Music
        if len(video_ids) > 0 and len(video_ids) % batch_size == 0:
            batch_num = len(video_ids) // batch_size
            log(f"\n  Saving batch {batch_num} ({batch_size} tracks)...")
            if playlist_id is None:
                playlist_id = create_yt_playlist(
                    yt,
                    name,
                    playlist.get("description", ""),
                    video_ids[-batch_size:]
                )
                if playlist_id:
                    log_success(f"  Playlist created with first batch.")
            else:
                try:
                    yt.add_playlist_items(playlist_id, video_ids[-batch_size:])
                    log_success(f"  Batch {batch_num} added to playlist.")
                except Exception as e:
                    log_error(f"  Failed to add batch {batch_num}: {e}")

    # Save any remaining tracks
    remaining = video_ids[-(len(video_ids) % batch_size):] if len(video_ids) % batch_size != 0 else []
    if remaining:
        log(f"\n  Saving final batch ({len(remaining)} tracks)...")
        if playlist_id is None:
            playlist_id = create_yt_playlist(yt, name, playlist.get("description", ""), remaining)
        else:
            try:
                yt.add_playlist_items(playlist_id, remaining)
                log_success(f"  Final batch added to playlist.")
            except Exception as e:
                log_error(f"  Failed to add final batch: {e}")

    if playlist_id:
        log_success(f"  Done! '{name}': {len(video_ids)}/{len(tracks)} matched, {skipped} skipped.")

    return {
        "spotify_name": name,
        "yt_playlist_id": playlist_id,
        "matched": len(video_ids),
        "skipped": skipped,
        "total": len(tracks),
        "not_found": not_found,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate Spotify music to YouTube Music")
    parser.add_argument("--liked", action="store_true", help="Migrate liked songs")
    parser.add_argument("--playlists", action="store_true", help="Migrate playlists")
    parser.add_argument("--all", action="store_true", help="Migrate everything")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--playlist-name", type=str, help="Migrate a specific playlist by name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    do_liked = args.liked or args.all
    do_playlists = args.playlists or args.all

    if not (do_liked or do_playlists):
        parser.print_help()
        sys.exit(0)

    log_section("Connecting to Spotify")
    sp = get_spotify_client()
    user = sp.current_user()
    log_success(f"Logged in as: {user['display_name']}")

    log_section("Connecting to YouTube Music")
    yt = get_ytmusic_client()
    log_success("YouTube Music ready.")

    results = []

    if do_liked:
        log_section("Fetching Liked Songs")
        liked = fetch_liked_songs(sp)
        log_success(f"Found {len(liked)} liked songs.")
        if not args.dry_run:
            result = migrate_playlist(yt, {
                "name": "Liked Songs (from Spotify)",
                "description": "Liked songs imported from Spotify.",
                "tracks": liked,
            }, delay=args.delay)
            results.append(result)

    if do_playlists or args.playlist_name:
        log_section("Fetching Playlists")
        playlists = fetch_playlists(sp)
        log_success(f"Found {len(playlists)} playlists.")
        if args.playlist_name:
            playlists = [pl for pl in playlists if pl["name"].lower() == args.playlist_name.lower()]
            if not playlists:
                log_error(f"No playlist found with name: '{args.playlist_name}'")
                sys.exit(1)
        for pl in playlists:
            if args.dry_run:
                log(f"  [DRY RUN] '{pl['name']}' – {len(pl['tracks'])} tracks")
            else:
                results.append(migrate_playlist(yt, pl, delay=args.delay))

    if results:
        with open("migration_report.json", "w") as f:
            json.dump(results, f, indent=2)
        log_success("\nReport saved to migration_report.json")

        log_section("Summary")
        for r in results:
            status = "✓" if r["yt_playlist_id"] else "✗"
            log(f"  {status} {r['spotify_name']}: {r['matched']}/{r['total']} tracks")


if __name__ == "__main__":
    main()