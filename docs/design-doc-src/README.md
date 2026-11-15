# Source for the GetOrderStatus API design document (Word)

The Word document is [`../GetOrderStatus-API-Design-Document.docx`](../GetOrderStatus-API-Design-Document.docx).

To regenerate it after a design change:

1. Edit the diagrams in `diagrams.html`, then render them to PNG (Node with Playwright):

   ```bash
   node docs/design-doc-src/render.js "$PWD/docs/design-doc-src"
   ```

2. Edit the content in `build.js`, then build the document (Node with the `docx` package):

   ```bash
   node docs/design-doc-src/build.js docs/design-doc-src docs/GetOrderStatus-API-Design-Document.docx
   ```

When you open the regenerated file, Word asks to update fields. Accept, so the table of contents is filled in.
