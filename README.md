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
facebook_c_user = x
facebook_xs = x
facebook_privacy = EVERYONE
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

### Facebook

Facebook uses [fbpost](https://github.com/htlin222/fbpost), pinned to a Git commit,
for its web GraphQL payloads, login-token extraction, and response parsing.
Requests use `curl_cffi` with the supported `chrome124` browser profile.
Selenium, Firefox, and geckodriver are no longer needed. The upstream package
depends on Playwright, but this integration does not launch it or require a
Playwright browser download.

Log in to `https://www.facebook.com/` in a browser, then find the site's cookies
in the developer tools. Copy `c_user` into `facebook_c_user` and `xs` into
`facebook_xs` under `[default]` in `config.ini`. Paste the values exactly as shown,
without quotes or URL decoding; `%` characters do not need escaping.
Other cookies can be supplied with the same `facebook_` prefix, including
`facebook_datr`, `facebook_sb`, `facebook_fr`, and `facebook_i_user`.

When any supported `facebook_<cookie>` setting is present, cookies come only
from `config.ini`; both `facebook_c_user` and `facebook_xs` must be nonempty.
The script does not merge them with a cookie file or fall back to that file when
the INI credentials are incomplete.

Alternatively, omit all `facebook_<cookie>` settings and export the site's cookies
as a JSON array (for example, Cookie-Editor's JSON export). Save the file as
`~/.config/feed2social/facebook-cookies.json`, or set `facebook_cookies_file` to
another path. Each cookie needs `name`, `value`, and `domain` fields. The export
must include `c_user` and `xs`; keeping the other Facebook cookies is recommended.
Keep whichever file contains the cookies private (`chmod 600`), outside the
repository, and refresh its values from the browser when the session expires.
The script does not log in with a password.

Posts use the identity in the cookies: `c_user` for a personal account, or `i_user`
when copied after switching to a Page. Check the selected identity before copying
cookies. The old `facebook_username` setting is unused and can be removed.
`facebook_privacy` accepts `EVERYONE` (public, the default), `FRIENDS`, or `SELF`
(only me). Each post contains the feed text followed by its source URL.

`./feed2facebook.sh --sync-only` records feed entries without reading cookies or
contacting Facebook. It processes all eligible entries in one run and marks them
as processed; it is not a dry run. Normal runs publish at most one entry, stopping
after recording a successful post. Remaining entries are left for later runs,
in feed order from oldest to newest.
Normal runs stop on failed or unconfirmed posts. An entry is recorded only after
Facebook returns a post/story ID or story URL without GraphQL errors. The script
accepts `story_create.post_id` or `story_create.story_id` even when `story` is null,
as well as the expanded `story.id` or `story.url`. There is no automatic request
retry. A timeout can occur after Facebook accepts a post, so check the
timeline before rerunning after an uncertain result; an unrecorded entry will be
attempted again on the next run.

Use `./feed2facebook.sh --debug` to inspect an unconfirmed posting result. This
still posts normally, with the configured audience. The debug output includes
the POST response's HTTP status, content type, byte count, every JSON frame's
structure, and the result selected by fbpost. String values are replaced with
their lengths, numbers with their types; nulls and booleans remain visible.
Request headers, cookies, login tokens, and non-JSON response content are not
printed. The existing feed-content log still contains the intended post text.
If the run reports an error, check the Facebook timeline before retrying: the
post may already exist even when no post ID was extracted.

Facebook can change its private GraphQL schema and document IDs. By default,
the script uses fbpost's `POST_DOC_ID`; `facebook_post_doc_id` can override it
with a numeric ID captured from a successful `ComposerStoryCreateMutation`
request in the browser. Schema changes may also require updating the pinned
fbpost revision. Offline tests verify the integration, not whether Facebook
currently accepts that upstream schema for your account.

Run the offline Facebook tests with `uv run --locked python -m unittest -v
test_feed2facebook`. They use temporary cookies and databases with intercepted
HTTP requests, without reading account configuration or posting to Facebook.

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
scripts and return the script's exit status.

The wrappers preserve the scheduler's `PATH` and also search `~/.local/bin` and
mise's latest uv installation at
`~/.local/share/mise/installs/uv/latest/.mise-bins`. Set `MISE_DATA_DIR` if mise
uses a different data directory. This works without interactive shell activation
or a globally configured mise uv version. For other installation locations, add
the directory reported by `command -v uv` to the scheduler's `PATH`.

Use an absolute script path in crontab, replacing the example paths with your own:

Create the log directory before enabling the scheduled jobs:

```bash
mkdir -p ~/log
```

```cron
PATH=/usr/local/bin:/usr/bin:/bin
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
or live posting compatibility.

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
