#!/usr/bin/env python3

import json
import time
import sys
import os
from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from ytmusicapi import YTMusic

from logger import log, log_success, log_warn, log_error, log_section

# ─── Spotify Auth ─────────────────────────────────────────────────────────────

def get_spotify_client() -> spotipy.Spotify:
    """
    Opens browser on first run for Spotify login.
    Token is cached in .spotify_token_cache for future runs.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")

    if not client_id:
        print("\nSpotify Client ID not found.")
        print("Get yours at: https://developer.spotify.com/dashboard")
        client_id = input("Paste your Spotify Client ID: ").strip()

    if not client_secret:
        client_secret = input("Paste your Spotify Client Secret: ").strip()

    scope = "user-library-read playlist-read-private playlist-read-collaborative"
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri="http://127.0.0.1:8888/callback",
        scope=scope,
        cache_path=".spotify_token_cache",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)

# ─── YouTube Music Auth ───────────────────────────────────────────────────────

def get_ytmusic_client() -> YTMusic:
    """
    Opens browser on first run for YouTube Music login.
    Token is cached in ytmusic_oauth.json for future runs.
    """
    oauth_file = Path("ytmusic_oauth.json")

    if not oauth_file.exists():
        log_section("YouTube Music Login")
        log("A browser window will open asking you to sign in with Google.")
        log("Please log in with the account that has your YouTube Music.")
        input("\nPress Enter to open the browser...")

        client_id = os.environ.get("YTMUSIC_CLIENT_ID")
        client_secret = os.environ.get("YTMUSIC_CLIENT_SECRET")

        if not client_id:
            print("\nGoogle OAuth Client ID not found.")
            print("Get yours at: https://console.cloud.google.com")
            client_id = input("Paste your Google Client ID: ").strip()

        if not client_secret:
            client_secret = input("Paste your Google Client Secret: ").strip()

        YTMusic.setup_oauth(
            filepath=str(oauth_file),
            client_id=client_id,
            client_secret=client_secret,
            open_browser=True,
        )

    return YTMusic(str(oauth_file))

# ─── Spotify Data ─────────────────────────────────────────────────────────────

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


def fetch_playlists(sp: spotipy.Spotify) -> list[dict]:
    playlists = []
    result = sp.current_user_playlists(limit=50)
    while result:
        for pl in result.get("items", []):
            if not pl:
                continue
            track_count = pl.get("tracks", {}).get("total", 0) if pl.get("tracks") else 0
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


def _parse_track(track: dict) -> dict:
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return {
        "name": track.get("name", ""),
        "artist": artists,
        "album": track.get("album", {}).get("name", ""),
    }

# ─── YouTube Music ────────────────────────────────────────────────────────────

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
        """Check if a playlist already exists, return its ID and existing video IDs."""
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

    def add_to_playlist_or_create(yt: YTMusic, playlist_id: Optional[str], name: str,
                                  description: str, batch: list[str], batch_num: int) -> Optional[str]:
        """Create a new playlist or add tracks to an existing one."""
        if playlist_id is None:
            playlist_id = create_yt_playlist(yt, name, description, batch)
            if playlist_id:
                log_success(f"  Playlist created with first batch.")
        else:
            try:
                yt.add_playlist_items(playlist_id, batch)
                log_success(f"  Batch {batch_num} added to playlist.")
            except Exception as e:
                log_error(f"  Failed to add batch {batch_num}: {e}")
        return playlist_id

    def migrate_playlist(yt: YTMusic, playlist: dict, delay: float = 0.3, batch_size: int = 100) -> dict:
        name = playlist["name"]
        tracks = playlist["tracks"]
        log(f"\n  Migrating: '{name}' ({len(tracks)} tracks)")

        # Check if playlist already exists to support resuming
        playlist_id, existing_ids = get_existing_yt_playlist(yt, name)
        if playlist_id:
            log(f"  Resuming existing playlist...")

        video_ids = []
        not_found = []
        skipped = 0

        for i, track in enumerate(tracks, 1):
            vid = search_track(yt, track)
            label = f"{track['artist']} – {track['name']}"

            # Skip if already in the playlist
            if vid and vid in existing_ids:
                skipped += 1
                log(f"    [{i}/{len(tracks)}] ~ Already in playlist, skipping: {label}")
                continue

            if vid:
                video_ids.append(vid)
                log(f"    [{i}/{len(tracks)}] ✓ {label}")
            else:
                not_found.append(label)
                log_warn(f"    [{i}/{len(tracks)}] ✗ Not found: {label}")

            time.sleep(delay)

            # Save every batch_size tracks
            if len(video_ids) > 0 and len(video_ids) % batch_size == 0:
                batch_num = len(video_ids) // batch_size
                log(f"\n  Saving batch {batch_num} ({batch_size} tracks)...")
                playlist_id = add_to_playlist_or_create(
                    yt, playlist_id, name,
                    playlist.get("description", ""),
                    video_ids[-batch_size:], batch_num
                )

        # Save any remaining tracks that didn't fill a full batch
        remaining = video_ids[-(len(video_ids) % batch_size):] if len(video_ids) % batch_size != 0 else []
        if remaining:
            log(f"\n  Saving final batch ({len(remaining)} tracks)...")
            playlist_id = add_to_playlist_or_create(
                yt, playlist_id, name,
                playlist.get("description", ""),
                remaining, -1
            )

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

    def select_playlists_interactively(playlists: list[dict]) -> list[dict]:
        """Display a menu of playlists and let the user pick which ones to migrate."""
        print("\nAvailable playlists:\n")
        for i, pl in enumerate(playlists, 1):
            track_count = len(pl["tracks"])
            print(f"  [{i}]  {pl['name']} ({track_count} tracks)")

        print("\nEnter playlist numbers to migrate separated by commas e.g. 1,3,5")
        print("Or type 'all' to migrate everything, or 'q' to quit.")

        while True:
            choice = input("\nYour selection: ").strip().lower()

            if choice == "q":
                log("Exiting.")
                sys.exit(0)

            if choice == "all":
                return playlists

            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [playlists[i] for i in indices if 0 <= i < len(playlists)]
                if not selected:
                    log_warn("No valid playlists selected, try again.")
                    continue
                return selected
            except ValueError:
                log_warn("Invalid input, please enter numbers separated by commas.")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate Spotify music to YouTube Music")
    parser.add_argument("--liked", action="store_true", help="Migrate liked songs")
    parser.add_argument("--playlists", action="store_true", help="Pick playlists interactively")
    parser.add_argument("--all", action="store_true", help="Migrate everything")
    parser.add_argument("--playlist-name", type=str, help="Migrate one playlist by name")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between API calls")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating playlists")
    args = parser.parse_args()

    do_liked = args.liked or args.all
    do_playlists = args.playlists or args.all

    if not (do_liked or do_playlists or args.playlist_name):
        parser.print_help()
        sys.exit(0)

    # ── Connect to both services ──
    log_section("Connecting to Spotify")
    sp = get_spotify_client()
    user = sp.current_user()
    log_success(f"Logged in as: {user['display_name']}")

    log_section("Connecting to YouTube Music")
    yt = get_ytmusic_client()
    log_success("YouTube Music ready.")

    results = []

    # ── Liked Songs ──
    if do_liked:
        log_section("Fetching Liked Songs")
        liked = fetch_liked_songs(sp)
        log_success(f"Found {len(liked)} liked songs.")
        if args.dry_run:
            log(f"  [DRY RUN] 'Liked Songs' – {len(liked)} tracks")
        else:
            results.append(migrate_playlist(yt, {
                "name": "Liked Songs (from Spotify)",
                "description": "Liked songs imported from Spotify.",
                "tracks": liked,
            }, delay=args.delay))

    # ── Playlists ──
    if do_playlists or args.playlist_name:
        log_section("Fetching Playlists")
        playlists = fetch_playlists(sp)
        log_success(f"Found {len(playlists)} playlists.")

        if args.playlist_name:
            # Single playlist by name
            playlists = [pl for pl in playlists if pl["name"].lower() == args.playlist_name.lower()]
            if not playlists:
                log_error(f"No playlist found with name: '{args.playlist_name}'")
                sys.exit(1)
        elif args.playlists and not args.all:
            # Interactive menu
            playlists = select_playlists_interactively(playlists)

        for pl in playlists:
            if args.dry_run:
                log(f"  [DRY RUN] '{pl['name']}' – {len(pl['tracks'])} tracks")
            else:
                results.append(migrate_playlist(yt, pl, delay=args.delay))

    # ── Save Report ──
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