"""
ingest.py — parse, embed, and store incoming documents.
Handles duplicate detection and program validation.
"""
# Full implementation will cover:
#   - routing by --type s|student / c|company / p|project
#   - calling parse.parse_file()
#   - calling embed.embed_text() + embed.save_embedding()
#   - duplicate detection via embed.cosine_similarity() against existing embeddings
#   - program typo check via fuzzy.detect_program_typo()
#   - writing metadata JSON via store.save_json()
#   - confirmation prompts for all ambiguous cases
pass
