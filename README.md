# Spotify2YTMusic

A command-line tool that migrates your Spotify library to YouTube Music: liked songs, all your playlists, or just the ones you pick.

It signs in to both services with OAuth, finds each track on YouTube Music, rebuilds the playlist there, and writes a report of what matched and what didn't.

## How it works

1. **Authenticate** with Spotify (via `spotipy`) and YouTube Music (via `ytmusicapi`) using OAuth. Tokens are cached locally so you only sign in once.
2. **Read** your Spotify liked songs and playlists, paging through the full library.
3. **Match** each track by searching YouTube Music for songs first and falling back to videos when no song result exists.
4. **Create** the playlist on YouTube Music and add matched tracks in batches, with a configurable delay between API calls to stay under rate limits.
5. **Report** results per playlist (matched, total, skipped) in the console and in `migration_report.json`.

## Requirements

- Python 3.9 or later
- A Spotify developer app (Client ID and Client Secret) with a redirect URI configured
- A Google Cloud OAuth client (Client ID and Client Secret) for YouTube Music

## Setup

```bash
git clone https://github.com/rodriq9890/Spotify2YTMusic.git
cd Spotify2YTMusic
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Set your credentials as environment variables, or paste them when the tool prompts you:

```bash
export SPOTIFY_CLIENT_ID=...
export SPOTIFY_CLIENT_SECRET=...
export YTMUSIC_CLIENT_ID=...
export YTMUSIC_CLIENT_SECRET=...
```

## Usage

```bash
python main.py --liked                      # migrate liked songs
python main.py --playlists                  # pick playlists interactively
python main.py --all                        # liked songs and every playlist
python main.py --playlist-name "Road Trip"  # one playlist by name
python main.py --all --dry-run              # preview without creating playlists
python main.py --all --delay 0.5            # slow down API calls
```

Run `python main.py --help` for every option.

## Output

- Console progress with per-playlist results, for example `'Road Trip': 48/52 matched, 4 skipped`
- `migration_report.json` with the same results for every playlist

## Privacy

Credentials and OAuth tokens stay on your machine. The `docs/` folder hosts the privacy policy and terms pages required for Google OAuth verification.
