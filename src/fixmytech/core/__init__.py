"""Schemas shared by the local diagnostic agent and the backend.

This package must stay free of server-only dependencies (database, FlipValue client)
so the agent can be shipped to a customer's computer on its own.
"""
