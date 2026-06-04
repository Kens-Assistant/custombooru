#!/usr/bin/dumb-init /bin/sh
set -e
cd /opt/app

if [ ! -w /data ]; then
	echo "ERROR: /data is not writable by UID:GID $(id -u):$(id -g)."
	echo "Set PUID/PGID in .env to match your host user and rebuild the server image."
	exit 1
fi

mkdir -p /data/temporary-uploads

alembic upgrade head

echo "Starting szurubooru API on port ${PORT} - Running on ${THREADS} threads"
exec hupper -m waitress --listen "*:${PORT}" --threads ${THREADS} szurubooru.facade:app
