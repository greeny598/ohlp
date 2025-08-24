# -*- coding: utf-8 -*-
"""
Created on Thu Aug 21 00:51:59 2025

@author: Professional
"""

# utils/docling_singletons.py
from threading import Lock
from docling.document_converter import DocumentConverter
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline


_pdf_converter = None
_docx_converter = None
_lock = Lock()


def get_pdf_converter():
    global _pdf_converter
    if _pdf_converter is not None:
        return _pdf_converter
    with _lock:
        if _pdf_converter is None:
            pipeline = StandardPdfPipeline()
            _pdf_converter = DocumentConverter(pipeline=pipeline)
    return _pdf_converter


def get_docx_converter():
    global _docx_converter
    if _docx_converter is not None:
        return _docx_converter
    with _lock:
        if _docx_converter is None:
            pipeline = SimplePipeline()