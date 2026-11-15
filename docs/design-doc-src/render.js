const { chromium } = require('playwright');
(async () => {
  const dir = process.argv[2];
  const b = await chromium.launch();
  const p = await b.newPage({ deviceScaleFactor: 2, viewport: { width: 1140, height: 900 } });
  await p.goto('file://' + dir + '/diagrams.html');
  for (const id of ['d1', 'd2', 'd3', 'd4']) {
    await p.locator('#' + id).screenshot({ path: `${dir}/${id}.png` });
  }
  await b.close();
})();
