"""Framework-free domain layer.

Nothing in this package may import FastAPI, uvicorn, or any provider/player
implementation. It defines the vocabulary of the system (models), the ports
that adapters implement (interfaces), and the orchestration logic (queue,
service, events).
"""
