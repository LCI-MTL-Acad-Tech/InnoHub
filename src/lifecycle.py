"""
lifecycle.py — activate, deactivate, complete entities.
Handles cascading cancellation of active assignments on deactivation.
"""
# Full implementation will cover:
#   - deactivate --student / --company (with cascading assignment cancellation)
#   - complete --student (document + embedding purge, CSV log preserved)
#   - activate --company (company back to active; projects stay at their own state)
#   - activate/deactivate --project
#   - reassign --student --semester
pass
