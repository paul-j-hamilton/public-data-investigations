.PHONY: preview render reproduce refresh-data

preview: reproduce
	quarto preview

render: reproduce
	quarto render

reproduce:
	python3 investigations/us-population-growth/scripts/analyze.py

refresh-data:
	python3 investigations/us-population-growth/scripts/fetch_data.py
	python3 investigations/us-population-growth/scripts/analyze.py

