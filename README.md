# The Public Record

A Quarto starter for fully transparent investigations of public data. Every investigation keeps its article, source ledger, preserved raw inputs, transformation code, processed data, limitations, and correction record together.

## Run it locally

Quarto and Python 3 are the only requirements for the included example.

```bash
make preview
```

To build the complete site without starting a preview server:

```bash
make render
```

`make reproduce` rebuilds analysis-ready data from committed raw snapshots without using the network. `make refresh-data` deliberately retrieves a new World Bank snapshot and then rebuilds the example; review and commit both the raw and processed changes.

## Turn this starter into your publication

1. Create a public GitHub repository and add this directory as its contents.
2. If you fork or rename the repository, update its URLs in `_quarto.yml`.
3. Replace the publication name, description, contact details, and example copy.
4. Push the `main` branch.
5. In the repository, open **Settings → Pages** and select **Deploy from a branch**, then choose `gh-pages` and `/ (root)` after the first workflow run creates the branch.
6. In **Settings → Actions → General**, ensure workflow permissions allow read and write access.

The workflow validates regenerated data, renders pull requests without publishing them, and publishes pushes to `main` through the `gh-pages` branch.

## Start a new investigation

Copy `investigations/oecd-rd-workforce` to a descriptive new folder as a template. Keep the same evidence structure:

```text
investigations/your-investigation/
├── index.qmd
├── sources.yml
├── data/
│   ├── raw/
│   └── processed/
└── scripts/
    ├── fetch_data.py
    └── analyze.py
```

Update the `reproduce` target and publishing workflow so every investigation's derived data is rebuilt and checked. Never put API keys, confidential records, or data you lack permission to redistribute in the repository.

## Suggested licensing

Use the MIT License for original code and CC BY 4.0 for original writing, while preserving and documenting the terms attached to third-party data. Add the final license files after choosing the publication owner and preferred attribution.
