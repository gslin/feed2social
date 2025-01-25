# feed2social

Sync feed to social networks.

## Platforms are supported

* Bluesky
* Facebook
* Plurk
* Threads

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
```

The `facebook_username` is used for generating the url `https://www.facebook.com/${facebook_username}`.

## Install

    pip install -r requirements.txt
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2bluesky.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2facebook.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2plurk.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2threads.sqlite3

## Run

Just run it periodically (usually with crontab):

```bash
./feed2bluesky.py
./feed2facebook.py
./feed2plurk.py
./feed2threads.py
```

We also support package manager environment like [pyenv](https://github.com/pyenv/pyenv) or [mise](https://github.com/jdx/mise), via shell script wrappers:

```bash
./feed2bluesky.sh
./feed2facebook.sh
./feed2plurk.sh
./feed2threads.sh
```

## Workarounds

Currently `plurk_oauth` requires `distutils`, which has been deprecated in Python 3.10, and has been removed in Python 3.12, so we have added `setuptools` as requirement, which adds `distutils` back (at least for now, not sure how long it will continue to support `distutils` compatibility).

## Notes

If you trace our codebase, you will notice that we have copied many same code across all Python scripts.  This is done intentionally, to keep every script runnable independently.

## License

See [LICENSE](LICENSE).
