# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A practical repository for comparing, testing, and monitoring GenAI applications across multiple evaluation tools and workflows. It explores the GenAI evaluation ecosystem hands-on: setting up different evaluation frameworks side by side and running them against real LLM and RAG applications.

## Structure Conventions

The repository is in its early stages. As it grows, each evaluation tool or workflow gets its own top-level directory containing:

- Runnable examples for that tool
- Notes on strengths, trade-offs, and setup
- Its own requirements and setup instructions (dependencies are documented per tool, not repo-wide)

When adding a new evaluation tool, follow this per-directory pattern rather than creating shared/global configuration.

## Focus Areas

Examples should demonstrate one or more of:

- **Comparing** outputs, prompts, and models across runs and configurations
- **Testing** application quality (e.g., faithfulness, relevance, correctness metrics)
- **Monitoring** production behavior (tracing, logging, dashboards)
