Module Reference
================

This section covers backend modules by subsystem for developers.

Core Modules
------------

.. automodule:: swcstudio.core.config
   :members:

.. automodule:: swcstudio.core.swc_io
   :members:

.. automodule:: swcstudio.core.reporting
   :members:

.. automodule:: swcstudio.core.validation_engine
   :members:

.. automodule:: swcstudio.core.validation_results
   :members:

.. automodule:: swcstudio.core.validation_registry
   :members:

.. automodule:: swcstudio.core.validation_catalog
   :members:

.. automodule:: swcstudio.core.auto_typing
   :members:

.. automodule:: swcstudio.core.auto_typing.runner
   :members:

.. automodule:: swcstudio.core.auto_typing.types
   :members:

.. automodule:: swcstudio.core.auto_typing.config
   :members:

.. automodule:: swcstudio.core.auto_typing.cell_class
   :members:

.. automodule:: swcstudio.core.auto_typing_train
   :members:

.. automodule:: swcstudio.core.model_paths
   :members:

The auto-typing engine runs the SWC auto-labeler, whose code is copied
into ``swcstudio.core.labeler`` by ``scripts/sync_labeler.py``. That
package is the labeler's own code and is not documented here; see the
labeler repository (https://github.com/LazziLab/swc-autolabeler).
SWC-Studio reaches it only through ``swcstudio.core.auto_typing``.

Tool Feature Modules
--------------------

Batch Processing
^^^^^^^^^^^^^^^^

.. automodule:: swcstudio.tools.batch_processing.features.batch_validation
   :members:

.. automodule:: swcstudio.tools.batch_processing.features.swc_splitter
   :members:

.. automodule:: swcstudio.tools.batch_processing.features.auto_typing
   :members:

.. automodule:: swcstudio.tools.batch_processing.features.radii_cleaning
   :members:

Validation
^^^^^^^^^^

.. automodule:: swcstudio.tools.validation.features.run_checks
   :members:

.. automodule:: swcstudio.tools.validation.features.auto_fix
   :members:

.. automodule:: swcstudio.tools.validation.features.auto_typing
   :members:

.. automodule:: swcstudio.tools.validation.features.radii_cleaning
   :members:

Visualization
^^^^^^^^^^^^^

.. automodule:: swcstudio.tools.visualization.features.mesh_editing
   :members:

Morphology Editing
^^^^^^^^^^^^^^^^^^

.. automodule:: swcstudio.tools.morphology_editing.features.dendrogram_editing
   :members:

.. automodule:: swcstudio.tools.morphology_editing.features.simplification
   :members:

Plugin System Internals
-----------------------

.. automodule:: swcstudio.plugins.contracts
   :members:

.. automodule:: swcstudio.plugins.loader
   :members:

.. automodule:: swcstudio.plugins.registry
   :members:
