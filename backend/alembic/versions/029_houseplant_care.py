"""слой ухода за комнатными растениями: уход по родам и справочник болезней

В сентябре 2026 в приложение пришла аудитория из App Store, которая снимает
комнатные растения: 81% снимков за 17 сентября против 7% у прежних
пользователей. Приложение называет вид верно и показывает пустоту, потому что
карточек этих растений в травниках нет физически: сингониум ноль, хойя ноль,
диффенбахия ноль, замиокулькас ноль.

Уход хранится отдельно от карточки гербария и складывается с ней на чтении, как
читательский монограф. Причина простая: карточка травника отвечает на вопрос
«что это и что из этого делали», а человек с горшком спрашивает, сколько света и
как часто поливать. Смешивать эти два предмета в одной таблице значит ломать оба
(``docs/RFC-houseplants.md``).

Ключ утверждения — латынь рода или вида, а не идентификатор карточки. Люди
снимают сорта, книги описывают род: хойя керри и хойя карноза требуют одного и
того же. Ссылка на карточку гербария проставляется, когда карточка существует.

Каждое утверждение несёт дословную цитату и страницу. Модель уже фабриковала
рецепты, которых в книге не было, а совет по уходу человек выполняет буквально.

Revision ID: 029_houseplant_care
Revises: 028_species_phenology_cell
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "029_houseplant_care"
down_revision = "028_species_phenology_cell"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "houseplant_source",
        sa.Column("id", sa.Text(), primary_key=True),          # saakov1985, golovkin1989, …
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text()),
        sa.Column("year", sa.SmallInteger()),
        sa.Column("minio_object", sa.Text()),                  # бакет houseplant-books
        sa.Column("md5", sa.Text()),                           # контрольная сумма того файла, который читали
        # Кому книга адресована. «Оранжерея» у Саакова означает, что часть
        # советов написана не для квартиры, и читателю их показывать нельзя.
        sa.Column("audience", sa.Text(), server_default="room"),   # room | greenhouse | mixed
        sa.Column("note", sa.Text()),
    )

    op.create_table(
        "houseplant_care",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("taxon_latin", sa.Text(), nullable=False),    # Hoya / Hoya carnosa
        sa.Column("taxon_rank", sa.Text(), nullable=False),     # genus | species
        sa.Column("taxon_ru", sa.Text()),
        sa.Column("plant_id", UUID(as_uuid=True), nullable=True),   # карточка гербария, если есть
        sa.Column("field", sa.Text(), nullable=False),          # light | water | temperature | …
        sa.Column("season", sa.Text(), nullable=True),          # summer | winter | NULL
        sa.Column("value", JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("value_text", sa.Text(), nullable=False),     # фраза, которую прочитает человек
        sa.Column("quote", sa.Text(), nullable=False),          # дословно из скана
        sa.Column("source_id", sa.Text(), sa.ForeignKey("houseplant_source.id"), nullable=False),
        sa.Column("page", sa.Integer()),
        # Совет для оранжереи или производства: храним, но на подоконник не выдаём.
        sa.Column("greenhouse", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        # Ссылка на общую часть книги («уход общий», «земельная смесь № 2»),
        # пока она не разрешена во вводных главах.
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # Один источник говорит по одному полю и сезону один раз. Разные книги
    # спорят между собой намеренно: расхождение показываем двумя голосами.
    op.create_unique_constraint(
        "uq_houseplant_care_voice",
        "houseplant_care",
        ["taxon_latin", "field", "season", "source_id"],
    )
    op.create_index("ix_houseplant_care_taxon", "houseplant_care", ["taxon_latin"])
    op.create_index("ix_houseplant_care_plant", "houseplant_care", ["plant_id"])

    op.create_table(
        "houseplant_problem",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Общая таблица болезней («бутоны опадают») живёт без таксона: она
        # одинакова для всех комнатных и приходит из вводных глав.
        sa.Column("taxon_latin", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),           # pest | disease | disorder
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("symptom", sa.Text()),
        sa.Column("cause", sa.Text()),
        sa.Column("remedy", sa.Text()),
        # Препараты называем, но показываем с оговоркой: список разрешённых
        # у Воронцова датирован письмом Госхимкомиссии 1999 года.
        sa.Column("chemicals", ARRAY(sa.Text())),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("houseplant_source.id"), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_houseplant_problem_taxon", "houseplant_problem", ["taxon_latin"])
    op.create_index("ix_houseplant_problem_kind", "houseplant_problem", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_houseplant_problem_kind", table_name="houseplant_problem")
    op.drop_index("ix_houseplant_problem_taxon", table_name="houseplant_problem")
    op.drop_table("houseplant_problem")
    op.drop_index("ix_houseplant_care_plant", table_name="houseplant_care")
    op.drop_index("ix_houseplant_care_taxon", table_name="houseplant_care")
    op.drop_constraint("uq_houseplant_care_voice", "houseplant_care", type_="unique")
    op.drop_table("houseplant_care")
    op.drop_table("houseplant_source")
