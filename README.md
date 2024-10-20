# feed2social

Sync feed to social networks.

## Supported platforms

* Bluesky
* Facebook (via mbasic.facebook.com)
* Plurk

## Install

    pip install -r requirements.txt
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2bluesky.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2facebook.sqlite3
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2plurk.sqlite3

## Run

    ./feed2bluesky.py
    ./feed2facebook.py
    ./feed2plurk.py

## License

See [LICENSE](LICENSE).
