"""Importing this package registers every validator (decorator side-effect).

Add a new check = drop a module here and import it below.
"""
from app.services.data_quality.validators import alias_collision  # noqa: F401
from app.services.data_quality.validators import recipe_monograph  # noqa: F401
from app.services.data_quality.validators import mixed_script  # noqa: F401
from app.services.data_quality.validators import headnoun_cluster  # noqa: F401
from app.services.data_quality.validators import recipe_no_plant  # noqa: F401
from app.services.data_quality.validators import identity_kingdom  # noqa: F401
from app.services.data_quality.validators import identity_latin_unresolvable  # noqa: F401
from app.services.data_quality.validators import identity_conflict  # noqa: F401
from app.services.data_quality.validators import stub_zero_facts  # noqa: F401
from app.services.data_quality.validators import identity_over_merge  # noqa: F401
from app.services.data_quality.validators import qdrant_drift  # noqa: F401
