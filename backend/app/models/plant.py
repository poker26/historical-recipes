import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, Boolean, ForeignKey, ARRAY, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Plant(Base):
    """A medicinal/culinary plant — the single identity for the herbalism domain.

    One row per botanical species (keyed conceptually by ``name_latin``). Facts
    from different source books accumulate as child rows (medicinal uses,
    compounds, harvest notes, toxicity), each carrying its own ``source_book_id``
    and verbatim ``original_text`` — sources enrich a plant in layers rather than
    overwriting each other.
    """

    __tablename__ = "plants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)                       # headword as the source book titled it (often archaic, occasionally the bare Latin when the old dictionary gave no Russian name)
    name_latin: Mapped[str | None] = mapped_column(Text)          # binomial + author, e.g. "Rubus caesius L."
    name_modern: Mapped[str | None] = mapped_column(Text)         # modern Russian common name resolved from iNaturalist (preferred_common_name, locale=ru) — EXTERNAL data, parallel to the iNat photo, not a book-grounded fact
    names_historical: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # "другие названия" / folk / pre-reform
    family: Mapped[str | None] = mapped_column(Text)              # Russian family name, e.g. "розовые"
    family_latin: Mapped[str | None] = mapped_column(Text)        # e.g. "Rosaceae"
    description: Mapped[str | None] = mapped_column(Text)         # botanical morphology + phenology (free text)
    parts_used: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # лист, корень, цвет, кора, семя, плод, трава
    is_toxic: Mapped[bool] = mapped_column(Boolean, default=False)
    kingdom: Mapped[str] = mapped_column(String(20), default="растение", server_default="растение")  # растение | гриб — biological kingdom; a mushroom guide tags its rows гриб so a future agent can ask for plants, fungi, or both unambiguously
    qdrant_point_id: Mapped[str | None] = mapped_column(String(100))
    qdrant_collection: Mapped[str | None] = mapped_column(String(50))

    # iNaturalist enrichment (external layer; populated by enrich_plants_inat).
    # inat_taxon_id is the bridge key (resolve name_latin → taxon once), reused
    # later for "find nearby observations". Photo stored only when its license
    # permits our use; attribution is always shown alongside.
    inat_taxon_id: Mapped[int | None] = mapped_column(Integer)
    photo_url: Mapped[str | None] = mapped_column(Text)
    photo_attribution: Mapped[str | None] = mapped_column(Text)
    photo_license: Mapped[str | None] = mapped_column(String(40))
    photo_source: Mapped[str | None] = mapped_column(String(30))
    inat_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    properties: Mapped[list["PlantProperty"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    mentions: Mapped[list["PlantBookMention"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    medicinal_uses: Mapped[list["PlantMedicinalUse"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    compounds: Mapped[list["PlantCompound"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    harvests: Mapped[list["PlantHarvest"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    habitats: Mapped[list["PlantHabitat"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    toxicities: Mapped[list["PlantToxicity"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    culinary_uses: Mapped[list["PlantCulinaryUse"]] = relationship(back_populates="plant", cascade="all, delete-orphan")


class MedicinalAction(Base):
    """Controlled, hierarchical vocabulary of medicinal actions.

    Two levels: a top-level functional ``group`` (e.g. "действие на ЖКТ") with
    ``parent_id`` NULL, and concrete actions (e.g. "вяжущее", "мочегонное")
    pointing at their group. Normalizing actions here lets the catalogue answer
    "show all diuretic plants" instead of fighting 20 LLM spellings of one term.
    """

    __tablename__ = "medicinal_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medicinal_actions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text, unique=True)         # canonical action term, e.g. "мочегонное"
    name_modern: Mapped[str | None] = mapped_column(Text)        # modern/clinical synonym, e.g. "диуретическое"
    synonyms: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # alternate spellings to match action_raw against
    system: Mapped[str | None] = mapped_column(String(50))       # body system tag: ЖКТ, ССС, ЦНС, дыхание, ...
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))


class Indication(Base):
    """Controlled, hierarchical vocabulary of medicinal indications (показания) —
    what a remedy is used *for* (symptoms and conditions).

    The disease/symptom analog of ``MedicinalAction``: a top-level group (e.g.
    "болезни органов дыхания", ``parent_id`` NULL) with concrete indications (e.g.
    "кашель") pointing at their group. Built/grown by the medical-normalizer pass
    from the free-text ``PlantMedicinalUse.indications`` already in the corpus,
    then used to normalize those strings so the catalogue can answer "какие
    растения при кашле".

    Its headline field is ``archaic``: pre-modern names of the same concept
    (водянка → отёки, грудная жаба → стенокардия, золотуха → скрофулёз). A query
    for either an archaic or a modern term resolves to the same concept, bridging
    a historical лечебник to a modern question.
    """

    __tablename__ = "indications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("indications.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text, unique=True)         # canonical term (modern where one exists), e.g. "отёки"
    name_modern: Mapped[str | None] = mapped_column(Text)        # explicit modern/clinical name if `name` kept historical
    synonyms: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # alternate spellings/forms
    archaic: Mapped[list[str] | None] = mapped_column(ARRAY(String))   # archaic names mapped to this concept (the bridge)
    system: Mapped[str | None] = mapped_column(String(50))      # body system: дыхание/ЖКТ/ССС/ЦНС/кожа/мочеполовая/...
    definition: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))


class Compound(Base):
    """Controlled, hierarchical vocabulary of plant chemical constituents.

    The chemistry analog of ``MedicinalAction``: a top-level class (e.g.
    "флавоноиды", ``parent_id`` NULL) with concrete substances (e.g. "рутин")
    pointing at their class. Built/grown by a phytochemistry reference book via
    the reference-normalizer pipeline, then used to normalize the free-text
    ``PlantCompound.compound`` strings so the catalogue can answer
    "show all plants containing cardiac glycosides" instead of fighting N
    spellings of one substance.
    """

    __tablename__ = "compounds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("compounds.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text, unique=True)            # canonical term, e.g. "сердечные гликозиды"
    name_latin: Mapped[str | None] = mapped_column(Text)            # IUPAC/Latin/transliteration if given
    synonyms: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # alternate spellings/names
    compound_class: Mapped[str | None] = mapped_column(String(60))  # алкалоиды/гликозиды/флавоноиды/сапонины/...
    definition: Mapped[str | None] = mapped_column(Text)            # short description from the source
    original_text: Mapped[str | None] = mapped_column(Text)         # verbatim source sentence(s)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))


class PlantMedicinalUse(Base):
    """One medicinal-use fact: a plant part, prepared a certain way, has an
    action for certain indications — as stated by one source."""

    __tablename__ = "plant_medicinal_uses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    part: Mapped[str | None] = mapped_column(String(50))         # лист / корень / цвет / трава / плод ...
    action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medicinal_actions.id", ondelete="SET NULL"))
    action_raw: Mapped[str | None] = mapped_column(Text)         # action as written, before normalization to action_id
    indications: Mapped[str | None] = mapped_column(Text)        # what it treats: "лихорадка, кашель"
    indication_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))  # normalized concepts (free text → Indication ids)
    preparation: Mapped[str | None] = mapped_column(String(50))  # настой / отвар / настойка / мазь / припарка / сок
    dosage: Mapped[str | None] = mapped_column(Text)             # method & dose of use
    contraindications: Mapped[str | None] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)      # verbatim source sentence(s)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    confidence: Mapped[float | None] = mapped_column(Float)

    plant: Mapped["Plant"] = relationship(back_populates="medicinal_uses")
    action: Mapped["MedicinalAction | None"] = relationship()


class PlantCompound(Base):
    """An active/chemical constituent of a plant part (scientific-source layer)."""

    __tablename__ = "plant_compounds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    compound: Mapped[str] = mapped_column(Text)                  # дубильные вещества, гликозиды, эфирное масло ...
    compound_group: Mapped[str | None] = mapped_column(String(60))  # алкалоиды / флавоноиды / сапонины / витамины ...
    compound_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("compounds.id", ondelete="SET NULL"))  # normalized vocab id (compound stays raw)
    part: Mapped[str | None] = mapped_column(String(50))         # в плодах / в листьях / в корнях
    notes: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="compounds")
    compound_ref: Mapped["Compound | None"] = relationship()


class PlantHarvest(Base):
    """Collection & preparation note: which part, when, and how to gather/dry/store."""

    __tablename__ = "plant_harvests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    part: Mapped[str | None] = mapped_column(String(50))
    season: Mapped[str | None] = mapped_column(Text)            # "май–июнь, во время цветения"
    method: Mapped[str | None] = mapped_column(Text)            # how to collect / dry / store
    original_text: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="harvests")


class PlantHabitat(Base):
    """Where a plant grows, per a source — region/biotope + conservation status."""

    __tablename__ = "plant_habitats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    region: Mapped[str | None] = mapped_column(Text)           # geographic range
    biotope: Mapped[str | None] = mapped_column(Text)          # "опушки лесов, разнотравные степи"
    status: Mapped[str | None] = mapped_column(String(60))     # "редкое", "Красная книга", ...
    original_text: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="habitats")


class PlantToxicity(Base):
    """Toxicity note: which parts are poisonous, symptoms, antidote, severity."""

    __tablename__ = "plant_toxicities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    toxic_parts: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    symptoms: Mapped[str | None] = mapped_column(Text)
    antidote: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(String(30))   # слабо / умеренно / сильно ядовито
    original_text: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="toxicities")


class PlantCulinaryUse(Base):
    """One culinary/edibility fact: a plant part, its edibility class, how it is
    prepared as food, and palatability/safety caveats — as stated by one source.

    The food-knowledge analog of ``PlantMedicinalUse`` for foraging / wild-food
    cookbooks (e.g. Замятина, «Кухня Робинзона»). Carries verbatim
    ``original_text`` so the same grounding guard that protects medicinal facts
    applies: an entry the model could not trace to the source is dropped.
    """

    __tablename__ = "plant_culinary_uses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    part: Mapped[str | None] = mapped_column(String(50))         # лист / корень / побег / цвет / плод / шляпка (грибы)
    edibility: Mapped[str | None] = mapped_column(String(20))    # съедобно / условно-съедобно / несъедобно / ядовито
    preparation: Mapped[str | None] = mapped_column(String(60))  # сырым / отварить / сушить / квасить / жарить / мука
    use: Mapped[str | None] = mapped_column(Text)               # dish/role: «в салаты», «суп», «заменитель муки»
    season: Mapped[str | None] = mapped_column(Text)            # when the part is good to gather/eat
    caution: Mapped[str | None] = mapped_column(Text)           # «горчит до отваривания», «только молодые», двойники
    original_text: Mapped[str | None] = mapped_column(Text)      # verbatim source sentence(s) — REQUIRED for grounding
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    confidence: Mapped[float | None] = mapped_column(Float)

    plant: Mapped["Plant"] = relationship(back_populates="culinary_uses")


class PlantProperty(Base):
    __tablename__ = "plant_properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    property_type: Mapped[str] = mapped_column(String(20))  # medicinal, flavor, aroma, color
    property: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="properties")


class PlantCompatibility(Base):
    __tablename__ = "plant_compatibility"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    plant_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    compatibility: Mapped[str] = mapped_column(String(20))  # synergy, neutral, conflict
    context: Mapped[str | None] = mapped_column(String(30))  # вкус, лечебное действие, аромат
    description: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))


class PlantBookMention(Base):
    __tablename__ = "plant_book_mentions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("book_chunks.id", ondelete="SET NULL"))
    original_name: Mapped[str | None] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)

    plant: Mapped["Plant"] = relationship(back_populates="mentions")
