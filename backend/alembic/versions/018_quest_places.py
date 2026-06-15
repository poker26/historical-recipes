"""quest places: PostGIS table of named OSM places (parks/forests/reserves)

Phase 2 of the quests backend (docs/PLAN-quests-backend.md §2): named-place
boundaries from OpenStreetMap as polygons, for "GPS point → covering named place"
(badge geography). PostGIS geometry + GIST index. Geom is managed by raw SQL /
PostGIS functions (no geoalchemy2 dependency); the ORM model maps only the scalars.

Revision ID: 018_quest_places
Revises: 017_quests_identity
Create Date: 2026-06-15
"""
from alembic import op

revision = "018_quest_places"
down_revision = "017_quests_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # postgis is enabled out-of-band (supabase_admin); guard anyway.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(
        """
        CREATE TABLE quest_places (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            osm_id      text UNIQUE,                       -- e.g. 'relation/123'
            name        text NOT NULL,
            kind        text,                              -- park / forest / reserve / zone
            area        double precision,                 -- m^2 (for "smallest covering")
            geom        geometry(MultiPolygon, 4326) NOT NULL,
            created_at  timestamptz DEFAULT now(),
            updated_at  timestamptz DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_quest_places_geom ON quest_places USING GIST (geom)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quest_places")
