"""The one personal identifier both guards need, assembled so the string itself
never appears in the tree (issues #188, #189).

Kept in its own module because `test_repo_hygiene.py` imports the Android
guard's detectors and the Android guard imports this domain; a constant that
lived in either test file made the two import each other.
"""

# The owner's LAN-only domain.
PERSONAL_DOMAIN = "lafuenti" + ".com"
