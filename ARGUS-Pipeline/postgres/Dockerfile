# PostgreSQL 16 + PostGIS on the SAME Debian base as the stock postgres:16 image.
#
# Why not postgis/postgis:16-3.x? Those images are built on older Debian releases (16-3.4 is Debian 11, glibc 2.31; 16-3.5 is
# Debian 12, glibc 2.36), while a database created by postgres:16 today lives on Debian 13 (glibc 2.41). Postgres orders text
# with the C library's collation, so moving a data directory to an older glibc can silently misorder every text index
# ("collation version mismatch"). Building on postgres:16 keeps the collation identical and adds only the PostGIS package.
FROM postgres:16
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 postgresql-16-postgis-3-scripts \
 && rm -rf /var/lib/apt/lists/*
