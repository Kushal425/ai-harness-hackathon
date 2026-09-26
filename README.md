# AI Coding Harness

Autonomous AI coding-agent harness built for the **LCC × DevClub AI Coding Harness Hackathon 2026**.

The project is designed to turn a foundation language model into an autonomous software-engineering agent capable of understanding coding tasks, exploring an existing repository, using development tools, managing context, recovering from failures, modifying code, and verifying its changes.

---

## Table of Contents

- [Overview](#overview)
- [Problem](#problem)
- [Objectives](#objectives)
- [Core Capabilities](#core-capabilities)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Requirements](#requirements)
- [Environment Configuration](#environment-configuration)
- [Setup](#setup)
- [Running the Harness](#running-the-harness)
- [Running Tests](#running-tests)
- [Cleaning the Environment](#cleaning-the-environment)
- [Development Workflow](#development-workflow)
- [Security](#security)
- [Evaluation Workflow](#evaluation-workflow)
- [Design Principles](#design-principles)
- [Project Status](#project-status)

---

# Overview

A foundation model can generate code, but generating code is only one part of completing a software-engineering task.

A coding harness provides the surrounding system required for the model to behave more like an autonomous software engineer.

This project focuses on building that system.

The harness is responsible for:

- understanding software-engineering tasks
- planning the work
- navigating an existing repository
- retrieving relevant context
- using development tools
- modifying files
- executing commands
- reacting to failures
- verifying changes
- tracking execution
- producing a final result

The goal is not to build a conventional application.

The goal is to build a reliable system around a foundation model that enables autonomous software-engineering work.

---

# Problem

The hackathon problem is to build an autonomous coding-agent harness around the standardized foundation model.

The harness should enable the underlying model to:

1. Understand a software-engineering task.
2. Navigate an existing repository.
3. Intelligently use available tools.
4. Manage context effectively.
5. Recover from failures without requiring human intervention.
6. Produce correct and verified code changes.
7. Use tokens and computational resources efficiently.

The system should therefore act as the layer between:

```text
Software Engineering Task
        ↓
Coding-Agent Harness
        ↓
Foundation Model + Tools
        ↓
Verified Repository Change