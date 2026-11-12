import { test, expect } from '@playwright/test'
import { move, tag } from './helpers'

test('prep sheet → confirm → print → scan IN → dashboard and batch history close the loop', async ({ page, request }) => {
  const project = 'DEMO-LOOP-' + tag()
  const csv = ['project_number,material_ref,test_code,preconditioning_code,specimen_count',
    `${project},DEMO loop cotton,D5034,AR,3`, `${project},DEMO loop nylon,D5034,5L,2`].join('\n')

  // 1 Upload
  await page.goto('/#batch/new')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Upload the prep sheet' })).toBeVisible()
  await page.getByLabel('Import prep workbook').setInputFiles({ name: 'DEMO-loop.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })

  // 2 Confirm
  await expect(page.getByRole('heading', { name: /Check the sheet/ })).toBeVisible()
  await expect(page.getByText('Ready to register')).toBeVisible()
  await expect(page.getByText('2 sheet rows · 2 stacks · 5 pieces')).toBeVisible()
  await page.getByRole('button', { name: 'Register 2 stacks' }).click()

  // 3 Print: no printer in the test environment, so record a PDF print
  await expect(page.getByRole('heading', { name: 'Print labels', exact: true })).toBeVisible()
  await expect(page.locator('.label-preview-image img')).toHaveAttribute('src', /^blob:/)
  const batchId = /#batch\/([^/]+)\/print/.exec(page.url())![1]
  await page.getByRole('button', { name: 'Printed from PDF' }).click()
  await expect(page.getByText('Recorded: 5 labels printed from the PDF.')).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()

  // 4 Track
  await expect(page.getByRole('heading', { name: /Track this batch/ })).toBeVisible()
  await expect(page.getByText('Scan the stacks IN at their room.')).toBeVisible()

  // Dashboard: labeled but not scanned in is a gap until the stacks reach a room
  await page.getByRole('link', { name: 'View on dashboard' }).click()
  await expect(page).toHaveURL(new RegExp('#dashboard\\?batch=' + batchId))
  const batch = await (await request.get('/api/batches/' + batchId)).json()
  expect(batch.stages).toMatchObject({ total: 5, labeled: 5, entered: 0 })
  for (const row of batch.stacks) await expect(page.getByTestId('not-scanned-' + row.id)).toBeVisible()
  await expect(page.getByTestId('batch-' + batchId)).toContainText('Labeled, not in room 5')

  for (const row of batch.stacks) await move(request, await (await request.get('/api/stacks/' + row.id)).json())
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect(page.getByText('Nothing waiting.')).toBeVisible()
  await expect(page.getByTestId('batch-' + batchId)).toContainText('Conditioning 5')
  await expect(page.getByRole('group', { name: 'Room' }).getByRole('button', { name: /^Shared/ })).toContainText('5 in')

  // Batch history lists the label confirmation and both IN scans
  await page.getByTestId('batch-' + batchId).getByRole('link', { name: 'History' }).click()
  await expect(page.getByText('Batch activity:')).toBeVisible()
  await expect(page.getByText('DEMO-loop.csv · 2 stacks')).toBeVisible()
  const history = (await (await request.get('/api/transactions?batch_id=' + batchId)).json()).items
  expect(history.filter((e: { kind: string; direction?: string }) => e.kind === 'movement' && e.direction === 'IN')).toHaveLength(2)
  expect(history.filter((e: { kind: string }) => e.kind === 'label_print')).toHaveLength(2)
  expect(new Set(history.map((e: { stack_id: number }) => e.stack_id))).toEqual(new Set(batch.stacks.map((s: { id: number }) => s.id)))
})

test('old #overview and #prep links land on the dashboard and the batch wizard', async ({ page }) => {
  await page.goto('/#overview')
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible()
  await page.goto('/#prep')
  await expect(page.getByRole('heading', { name: 'New batch', exact: true })).toBeVisible()
})
