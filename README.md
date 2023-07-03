# feed2social

Sync feed to social networks.

## Supported platforms

* Plurk

## Install

    pip install -r requirements.txt
    echo "CREATE TABLE entry (entry_id VARCHAR, created_at INT);" | sqlite3 ~/.config/feed2social/feed2plurk.db

## Run

    ./feed2plurk.py

## License

See [LICENSE](LICENSE).
