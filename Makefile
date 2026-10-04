.PHONY: preview render reproduce refresh-data

preview: reproduce
	quarto preview

render: reproduce
	quarto render

# Rebuild every investigation's derived data from its committed raw snapshot (no network).
reproduce:
	python3 investigations/oecd-rd-workforce/scripts/analyze.py
	python3 investigations/oecd-rd-workforce/scripts/make_thumbnail.py

# Deliberately download new raw snapshots, then rebuild. Review and commit the changes.
refresh-data:
	python3 investigations/oecd-rd-workforce/scripts/fetch_data.py
	$(MAKE) reproduce
