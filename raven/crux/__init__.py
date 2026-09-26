"""Crux: disagreement-driven patch selection.

Generate a few candidate fixes, execute them against each other, and spend
the model's judgement only where their behaviour disagrees -- on the
smallest input that separates them, as a concrete multiple-choice question
backed by repository evidence. Stages live in one module each; the loop is
raven/crux/pipeline.py.
"""
