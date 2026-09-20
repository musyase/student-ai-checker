import fs from "node:fs/promises";
import { dirname } from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputPath = process.argv[3];
if (!inputPath || !outputPath) {
  throw new Error("Usage: build_teacher_roster_workbook.mjs <teacher-roster.csv> <output.xlsx>");
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];
    if (quoted) {
      if (char === '"' && next === '"') { cell += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"' && cell === "") quoted = true;
    else if (char === ",") { row.push(cell); cell = ""; }
    else if (char === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += char;
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  const headers = rows.shift().map((header, index) => index === 0 ? header.replace(/^\uFEFF/, "") : header);
  return rows.filter((item) => item.some((value) => value !== "")).map((item) =>
    Object.fromEntries(headers.map((header, index) => [header, item[index] ?? ""])));
}

function typed(value, numeric = false) {
  if (!numeric) return value;
  if (value === "" || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : value;
}

const roster = parseCsv(await fs.readFile(inputPath, "utf8"));
const manual = roster.filter((row) => row["Статус"] !== "Готово");
const groups = new Map();
for (const row of roster) {
  if (!groups.has(row["Группа"])) groups.set(row["Группа"], { "Группа": row["Группа"], "Работ": 0, "Готово": 0, "Нужна проверка": 0, "Оценка 5": 0, "Оценка 4": 0, "Оценка 3": 0, "Оценка 2": 0 });
  const summary = groups.get(row["Группа"]);
  summary["Работ"] += 1;
  summary[row["Статус"] === "Готово" ? "Готово" : "Нужна проверка"] += 1;
  if (row["Оценка"]) summary[`Оценка ${row["Оценка"]}`] += 1;
}
const groupRows = [...groups.values()].sort((a, b) => a["Группа"].localeCompare(b["Группа"], "ru"));

const workbook = Workbook.create();
const fontFamily = "Arial";
const headerFill = "#1F4E78";
const titleColor = "#17365D";
const warningFill = "#FFF2CC";
const dangerFill = "#FCE4D6";
const successFill = "#E2F0D9";
const lightFill = "#F3F6FA";

function makeSheet(name, title, subtitle, headers, rows, widths, tableName, bodyHeight = 38) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1").format = { font: { name: fontFamily, size: 16, bold: true, color: titleColor } };
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange("A2").format = { font: { name: fontFamily, size: 10, italic: true, color: "#666666" } };
  const start = 4;
  const values = [headers, ...rows];
  const endColumn = String.fromCharCode(64 + headers.length);
  sheet.getRange(`A${start}:${endColumn}${start + values.length - 1}`).values = values;
  sheet.getRange(`A${start}:${endColumn}${start}`).format = {
    fill: headerFill,
    font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
  };
  const body = sheet.getRange(`A${start + 1}:${endColumn}${start + rows.length}`);
  body.format = { font: { name: fontFamily, size: 10, color: "#222222" }, verticalAlignment: "center", wrapText: true };
  body.format.borders = { insideHorizontal: { style: "thin", color: "#D9E2F3" }, bottom: { style: "thin", color: "#D9E2F3" } };
  for (let index = 0; index < widths.length; index += 1) {
    sheet.getRangeByIndexes(0, index, start + rows.length, 1).format.columnWidth = widths[index];
  }
  sheet.getRange(`A${start}:${endColumn}${start + rows.length}`).format.rowHeight = 22;
  sheet.getRange(`A${start + 1}:${endColumn}${start + rows.length}`).format.rowHeight = bodyHeight;
  sheet.tables.add(`A${start}:${endColumn}${start + rows.length}`, true, tableName);
  sheet.freezePanes.freezeRows(start);
  return sheet;
}

const rosterHeaders = ["Группа", "Фамилия / студенты", "Итоговые баллы", "Известно баллов", "Макс. баллов", "Оценка", "Статус", "Критерии для проверки", "Причина", "Файл"];
const rosterRows = roster.map((row) => [
  row["Группа"], row["Фамилия / студенты"], typed(row["Итоговые баллы"], true), typed(row["Известно баллов"], true), typed(row["Макс. баллов"], true), typed(row["Оценка"], true), row["Статус"], row["Критерии для проверки"], row["Причина"], row["Файл"],
]);
const rosterSheet = makeSheet("Реестр", "Реестр работ студентов", "ПР1 · сортировка по группе и фамилии · одна строка соответствует одному DOCX", rosterHeaders, rosterRows, [16, 32, 15, 15, 14, 10, 20, 30, 58, 55], "TeacherRoster", 50);
rosterSheet.getRange(`G5:G${4 + rosterRows.length}`).conditionalFormats.add("containsText", { text: "Готово", format: { fill: successFill } });
rosterSheet.getRange(`G5:G${4 + rosterRows.length}`).conditionalFormats.add("containsText", { text: "Нужна", format: { fill: warningFill } });
rosterSheet.getRange(`G5:G${4 + rosterRows.length}`).conditionalFormats.add("containsText", { text: "Техническая", format: { fill: dangerFill } });
rosterSheet.getRange(`F5:F${4 + rosterRows.length}`).conditionalFormats.add("cellIs", { operator: "equal", formula: 5, format: { fill: successFill, font: { bold: true } } });
rosterSheet.getRange(`F5:F${4 + rosterRows.length}`).conditionalFormats.add("cellIs", { operator: "equal", formula: 4, format: { fill: lightFill, font: { bold: true } } });

const manualHeaders = ["Группа", "Фамилия / студенты", "Статус", "Критерии для проверки", "Причина", "Файл"];
const manualRows = manual.map((row) => [row["Группа"], row["Фамилия / студенты"], row["Статус"], row["Критерии для проверки"], row["Причина"], row["Файл"]]);
const manualSheet = makeSheet("К проверке", "Работы, которые нужно разобрать", "Сначала проверяйте эти строки; причины и критерии указаны справа", manualHeaders, manualRows, [16, 32, 22, 32, 68, 58], "ManualReview", 84);
manualSheet.getRange(`C5:C${4 + manualRows.length}`).conditionalFormats.add("containsText", { text: "Техническая", format: { fill: dangerFill } });
manualSheet.getRange(`C5:C${4 + manualRows.length}`).conditionalFormats.add("containsText", { text: "Нужна", format: { fill: warningFill } });

const groupHeaders = ["Группа", "Работ", "Готово", "Нужна проверка", "Оценка 5", "Оценка 4", "Оценка 3", "Оценка 2"];
const groupValues = groupRows.map((row) => groupHeaders.map((header) => typed(row[header], header !== "Группа")));
const groupSheet = makeSheet("По группам", "Сводка по группам", "Количество работ и текущий статус обработки", groupHeaders, groupValues, [18, 12, 12, 18, 12, 12, 12, 12], "GroupSummary", 26);
groupSheet.getRange(`B5:H${4 + groupValues.length}`).format.numberFormat = "0";

for (const sheet of [rosterSheet, manualSheet, groupSheet]) {
  sheet.getUsedRange().format.font = { name: fontFamily };
}

const previewDir = `${outputPath}.preview`;
await fs.mkdir(previewDir, { recursive: true });
for (const sheetName of ["Реестр", "К проверке", "По группам"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(`${previewDir}/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const check = await workbook.inspect({ kind: "sheet,table", maxChars: 6000, tableMaxRows: 5, tableMaxCols: 10 });
console.log(check.ndjson);
await fs.mkdir(dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(`saved ${outputPath}`);
