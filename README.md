# feed2social

Sync feed to social networks.

## Platforms are supported

* Bluesky
* Facebook
* Plurk
* Threads
* Twitter

## Config

The `~/.config/feed2social/config.ini` file:

```ini
[default]
bluesky_username = username.bsky.social
bluesky_password = x
facebook_username = username
feed_url = https://abpe.org/@gslin.rss
plurk_app_key = x
plurk_app_secret = x
plurk_token = x
plurk_token_secret = x
threads_access_token = x
threads_user_id = x
twitter_auth_token = x
twitter_ct0 = x
```

The `facebook_username` is used for generating the url `https://www.facebook.com/${facebook_username}`.

### Twitter

Twitter uses [twifork](https://github.com/PawiX25/twifork) with the `impersonate`
extra and its `curl_cffi` backend. The client uses the supported `chrome124`
browser profile to post text, upload images, and reply with the feed entry URL.
The package is installed as `twifork`, but imported as `twikit`.

Log in to `https://x.com/` in a browser, then open the developer tools and find
the site's cookies under Application/Storage. Copy the `auth_token` cookie into
`twitter_auth_token` and the `ct0` cookie into `twitter_ct0` in `config.ini`.
These are browser session cookies, not the OAuth tokens used by the old version.
The four old `twitter_api_key`, `twitter_api_key_secret`, `twitter_access_token`,
and `twitter_access_token_secret` settings are no longer used and can be removed.
Keep the cookie values private and refresh both from the browser if the session
expires or is rejected. No password or developer API key is required.

`./feed2twitter.sh --sync-only` records feed entries without contacting Twitter
or requiring Twitter cookies. It marks entries as processed; it is not a dry run.
Normal runs stop on Twitter errors, including upload failures and rate limits.
A failed main post is not recorded. Once the main post succeeds it is recorded
before the source-link reply, so a reply failure will not duplicate the main post
on the next run; failed replies are not retried automatically.

Run the offline Twitter tests with `uv run --locked python -m unittest -v
test_feed2twitter`. These use temporary databases and mocked requests, without
reading account configuration or posting to Twitter.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
this command from the repository directory:

```bash
uv run --locked python check-env.py
```

uv installs its own Python 3.12 and creates `.venv` using the versions in
`uv.lock`. The project does not use packages installed in a global Python,
mise Python, or pyenv Python. Commit `pyproject.toml`, `uv.lock`, and
`.python-version`; `.venv` is generated locally and ignored by Git.

For a new installation, create the databases after configuring the account:

    mkdir -p ~/.config/feed2social
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2bluesky.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2facebook.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2plurk.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2threads.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2twitter.sqlite3

## Run

Run the shell wrappers periodically, usually with crontab:

```bash
./feed2bluesky.sh
./feed2facebook.sh
./feed2plurk.sh
./feed2threads.sh
./feed2twitter.sh
./threads-refresh-access-token.sh
```

The wrappers use `uv run --locked python` and work from any working directory.
uv creates `.venv` if needed and synchronizes dependencies with `uv.lock` before
running the script. No manual environment setup or activation is required.
The lockfile must be present and match `pyproject.toml`; `--locked` reports an
error instead of updating it. The wrappers pass arguments through to the Python
scripts and return the script's exit status. The Facebook wrapper also retains
its browser cleanup.

uv must be available on the scheduler's `PATH`. Use `command -v uv` to find its
installation directory. For a standard installation in `~/.local/bin`, use an
explicit `PATH` and an absolute script path in crontab, replacing the example
paths with your own:

Create the log directory before enabling the scheduled jobs:

```bash
mkdir -p ~/log
```

```cron
PATH=/home/your-user/.local/bin:/usr/local/bin:/usr/bin:/bin
*/2 * * * * /path/to/feed2social/feed2bluesky.sh >> "$HOME/log/feed2bluesky.log" 2>&1
```

The first run may download Python and dependencies. You can prepare the
environment in advance with `uv sync --locked`, but this is optional. Account
configuration and database initialization are still required as described above.
To run a Python script directly, use `uv run --locked python feed2bluesky.py`.

## Maintenance

After pulling a project update, install its locked dependencies and check the
environment:

```bash
uv run --locked python check-env.py
```

The environment check imports all scripts without running their main functions.
It does not read account configuration, modify databases, post messages, refresh
tokens, or start a browser. It checks Python dependencies, not service credentials
or the external Firefox and geckodriver installation.

To update Python within the 3.12 series:

```bash
uv python upgrade 3.12
uv run --locked python check-env.py
```

`.python-version` selects `3.12` rather than a patch version, allowing uv-managed
environments to follow patch upgrades. The runtime may be shared by other uv
projects using Python 3.12, so schedule this update accordingly and restart any
long-running processes. Changing to Python 3.13 requires an explicit update to
`.python-version` and `requires-python` in `pyproject.toml`.

Update package versions separately from Python:

```bash
uv lock --upgrade
uv run --locked python check-env.py
```

Review and commit the `uv.lock` changes. To update one dependency, use
`uv lock --upgrade-package PACKAGE` instead. Add or remove dependencies with
`uv add PACKAGE` and `uv remove PACKAGE`.

## Workarounds

Currently `plurk_oauth` requires `distutils`, which has been deprecated in Python 3.10, and has been removed in Python 3.12, so we have added `setuptools` as requirement, which adds `distutils` back (at least for now, not sure how long it will continue to support `distutils` compatibility).

## Notes

If you trace our codebase, you will notice that we have copied many same code across all Python scripts.  This is done intentionally, to keep every script runnable independently.

## License

See [LICENSE](LICENSE).
