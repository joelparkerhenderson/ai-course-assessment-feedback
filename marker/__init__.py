"""marker: mark student essays 0-100 against a rubric with local Ollama models (default gpt-oss).

Modules:

* :mod:`marker.rubric`: load and validate analytic rubrics (TOML).
* :mod:`marker.essay`: read essays from .txt, .md and .docx.
* :mod:`marker.prompt`: build the marking prompt and the output JSON schema.
* :mod:`marker.ollama`: minimal, loopback-only Ollama client.
* :mod:`marker.models`: discover installed models; select them by tag, family, size and MLX build.
* :mod:`marker.grader`: sample, validate, aggregate and flag one essay.
* :mod:`marker.evaluate`: agreement metrics against human marks (QWK, MAE, ...).
* :mod:`marker.compare`: compare several models' results; panel mark; pairwise agreement.
* :mod:`marker.report`: render the score, explanation and thinking views.
* :mod:`marker.cli`: the ``marker`` command-line tool.

Library use::

    from marker.essay import load_essay
    from marker.grader import Settings, grade_essay
    from marker.ollama import OllamaClient
    from marker.rubric import load_rubric

    client = OllamaClient()
    settings = Settings(model="gpt-oss:20b")
    result = grade_essay(
        client, settings, load_rubric("rubrics/uk-undergraduate-essay.toml"), load_essay("essay.md"),
        model_digest=client.model_digest(settings.model),
    )
    print(result["mark"], result["band"]["label"], result["flags"])
"""

__version__ = "0.1.0"
