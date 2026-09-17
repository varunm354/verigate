"""VeriGate experiment harness.

Loads task definitions, runs their visible/hidden pytest suites in
isolated subprocesses, and builds the reviewer-visible context that will
eventually be handed to an LLM reviewer (added in a later milestone).
"""
