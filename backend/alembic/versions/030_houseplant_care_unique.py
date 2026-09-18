"""уход комнатных: уникальность по цитате, а не «один голос на поле»

Первая заливка показала две ошибки в ограничении из миграции 029.

Первая: в Postgres NULL не равен NULL, поэтому строки без сезона (свет,
пересадка, размножение) ограничение просто не видело, и один и тот же совет
ложился дважды при повторном прогоне.

Вторая, важнее: «один голос источника на поле» оказался неверной моделью. У
Хессайона размножение замиокулькаса описано одной фразой, а у Саакова хойя
размножается и черенками, и воздушными отводками, и листом с почкой — это
разные утверждения, и схлопывать их в одно значит терять книгу.

Поэтому уникальность теперь по цитате: один и тот же кусок скана от одного
источника не ложится дважды, а разные утверждения живут рядом. Какой голос
показать читателю, решается на чтении.

Revision ID: 030_houseplant_care_unique
Revises: 029_houseplant_care
Create Date: 2026-09-18
"""
from alembic import op

revision = "030_houseplant_care_unique"
down_revision = "029_houseplant_care"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_houseplant_care_voice", "houseplant_care", type_="unique")
    # Пока дыра с NULL-сезоном была открыта, повторные прогоны успели записать
    # одни и те же цитаты дважды. Оставляем самую раннюю строку каждой пары.
    op.execute("""
        DELETE FROM houseplant_care a
        USING houseplant_care b
        WHERE a.ctid > b.ctid
          AND a.taxon_latin = b.taxon_latin
          AND a.field = b.field
          AND COALESCE(a.season, '') = COALESCE(b.season, '')
          AND a.source_id = b.source_id
          AND md5(a.quote) = md5(b.quote)
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_houseplant_care_quote
        ON houseplant_care (
            taxon_latin, field, COALESCE(season, ''), source_id, md5(quote)
        )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_houseplant_care_quote")
    op.create_unique_constraint(
        "uq_houseplant_care_voice",
        "houseplant_care",
        ["taxon_latin", "field", "season", "source_id"],
    )
