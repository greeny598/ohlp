"""
Utilities for obtaining thread-safe singleton `DocumentConverter` instances
for PDF and DOCX conversions in the Docling library.

This module abstracts away differences between multiple versions of the
`docling` library. In newer releases (>=2.42.0) the
`DocumentConverter` constructor accepts an `allowed_formats` argument to
restrict which document types are supported and automatically manages the
underlying pipelines. In older releases, a converter had to be constructed
explicitly by passing a pipeline instance. Furthermore, the API for
creating `StandardPdfPipeline` and the signature of
`DocumentConverter` vary across versions.

The functions defined here attempt the most modern API first and fall
back gracefully to the older pipeline-based API when necessary. Once a
converter has been created, it is cached in a module-level variable to
avoid repeated initialisation. Access to these variables is guarded by a
global lock to ensure thread safety.

Example usage
-------------

>>> from utils.docling_singletons import get_pdf_converter, get_docx_converter
>>> pdf_converter = get_pdf_converter()
>>> docx_converter = get_docx_converter()
>>> result = pdf_converter.convert("/path/to/file.pdf")
>>> text = result.document.export_to_text()

"""

from __future__ import annotations

import sys
from threading import Lock
from typing import Any, Optional

# Base classes for conversion and pipelines.
from docling.document_converter import DocumentConverter
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline

__all__ = ["get_pdf_converter", "get_docx_converter"]

# Attempt to import InputFormat for new API support. Some versions of
# docling package `InputFormat` under `docling.datamodel.base_models`.
try:
    from docling.datamodel.base_models import InputFormat  # type: ignore
except Exception:
    InputFormat = None  # type: ignore

# Module-level caches for the converters. They are initialised lazily.
_pdf_converter: Optional[DocumentConverter] = None
_docx_converter: Optional[DocumentConverter] = None

# A lock to guard concurrent initialisation of the converters.
_lock = Lock()


def _build_standard_pdf_pipeline() -> Any:
    """
    Create an instance of `StandardPdfPipeline` compatible with multiple
    versions of docling.

    In older versions of docling, `StandardPdfPipeline` can be
    instantiated without arguments. In newer versions, a `pipeline_options`
    argument is required. This helper tries the zero-argument form first
    and falls back to locating and instantiating an options class.

    Returns
    -------
    Any
        A pipeline instance suitable for constructing a `DocumentConverter`.

    Raises
    ------
    TypeError
        If a suitable pipeline cannot be constructed.
    """
    # Try the simplest construction first.
    try:
        return StandardPdfPipeline()
    except TypeError:
        pass

    # If that fails, attempt to locate a suitable options class. The name
    # and location of this class has changed across releases.
    options_cls = None
    candidates = [
        ("docling.pipeline.standard_pdf_pipeline", "StandardPdfPipelineOptions"),
        ("docling.pipeline.standard_pdf_pipeline", "PdfPipelineOptions"),
        ("docling.document_converter", "PipelineOptions"),
    ]
    for module_name, class_name in candidates:
        try:
            module = __import__(module_name, fromlist=[class_name])
            options_cls = getattr(module, class_name)
            break
        except Exception:
            continue

    if options_cls is not None:
        try:
            options_instance = options_cls()
        except Exception:
            # Some options classes might not have a zero-argument constructor;
            # pass None in that case and let the pipeline decide.
            options_instance = None
        return StandardPdfPipeline(pipeline_options=options_instance)

    # As a last resort, try passing None. Some versions accept this.
    try:
        return StandardPdfPipeline(pipeline_options=None)  # type: ignore[arg-type]
    except Exception as exc:
        raise TypeError(
            "Unable to instantiate StandardPdfPipeline: no suitable options class found "
            "and passing None failed"
        ) from exc


def _make_document_converter(pipeline: Any) -> DocumentConverter:
    """
    Construct a `DocumentConverter` for a given pipeline, accounting for
    differences in constructor signatures across docling versions.

    Parameters
    ----------
    pipeline : Any
        A pipeline instance, typically created by `_build_standard_pdf_pipeline`
        or `SimplePipeline`.

    Returns
    -------
    DocumentConverter
        A new DocumentConverter configured to use the given pipeline.

    Raises
    ------
    TypeError
        If the current docling version does not support constructing a
        `DocumentConverter` from a pipeline.
    """
    # Attempt to pass the pipeline as a keyword argument. This works in
    # older versions of docling.
    try:
        return DocumentConverter(pipeline=pipeline)  # type: ignore
    except TypeError:
        pass

    # Attempt to pass the pipeline positionally.
    try:
        return DocumentConverter(pipeline)  # type: ignore[misc]
    except TypeError:
        pass

    # Check for a factory method `from_pipeline` which is used in some
    # releases.
    factory = getattr(DocumentConverter, "from_pipeline", None)
    if callable(factory):
        return factory(pipeline)  # type: ignore[call-arg]

    raise TypeError(
        "Unable to create a DocumentConverter from the provided pipeline. "
        "Please verify your version of 'docling'."
    )


def _create_pdf_converter_via_formats() -> Optional[DocumentConverter]:
    """
    Attempt to construct a `DocumentConverter` restricted to handling only
    PDF documents using the `allowed_formats` argument available in
    docling>=2.42.0.

    Returns
    -------
    Optional[DocumentConverter]
        A converter restricted to PDF files, or None if either the
        `InputFormat` import failed or the constructor signature does not
        accept `allowed_formats`.
    """
    if InputFormat is None:
        return None
    try:
        return DocumentConverter(allowed_formats=[InputFormat.PDF])  # type: ignore[arg-type]
    except Exception:
        return None


def _create_docx_converter_via_formats() -> Optional[DocumentConverter]:
    """
    Attempt to construct a `DocumentConverter` restricted to handling only
    DOCX documents using the `allowed_formats` argument available in
    docling>=2.42.0.

    Returns
    -------
    Optional[DocumentConverter]
        A converter restricted to DOCX files, or None if either the
        `InputFormat` import failed or the constructor signature does not
        accept `allowed_formats`.
    """
    if InputFormat is None:
        return None
    try:
        return DocumentConverter(allowed_formats=[InputFormat.DOCX])  # type: ignore[arg-type]
    except Exception:
        return None


def get_pdf_converter() -> DocumentConverter:
    """
    Obtain a singleton `DocumentConverter` instance suitable for converting
    PDF files.

    This function first tries to create a converter using the modern
    `allowed_formats` API. If that fails (due to an older docling
    installation), it falls back to explicitly constructing a
    `StandardPdfPipeline` and wrapping it in a converter using the legacy
    constructor.

    Returns
    -------
    DocumentConverter
        A converter ready to process PDF documents.
    """
    global _pdf_converter
    if _pdf_converter is not None:
        return _pdf_converter
    with _lock:
        if _pdf_converter is None:
            # Prefer the new API if available
            converter = _create_pdf_converter_via_formats()
            if converter is None:
                # Fall back to the legacy pipeline-based approach
                pipeline = _build_standard_pdf_pipeline()
                converter = _make_document_converter(pipeline)
            _pdf_converter = converter
    # At this point _pdf_converter is guaranteed to be initialised
    return _pdf_converter  # type: ignore[return-value]


def get_docx_converter() -> DocumentConverter:
    """
    Obtain a singleton `DocumentConverter` instance suitable for converting
    DOCX files.

    This function first tries to create a converter using the modern
    `allowed_formats` API. If that fails (due to an older docling
    installation), it falls back to explicitly constructing a
    `SimplePipeline` and wrapping it in a converter using the legacy
    constructor.

    Returns
    -------
    DocumentConverter
        A converter ready to process DOCX documents.
    """
    global _docx_converter
    if _docx_converter is not None:
        return _docx_converter
    with _lock:
        if _docx_converter is None:
            converter = _create_docx_converter_via_formats()
            if converter is None:
                pipeline = SimplePipeline()
                converter = _make_document_converter(pipeline)
            _docx_converter = converter
    return _docx_converter  # type: ignore[return-value]