const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, WidthType, BorderStyle, ShadingType,
  LevelFormat, PageBreak, Header, Footer, PageNumber,
} = require('docx');

const outputPath = path.join(__dirname, 'VLAN10_20实验报告.docx');
const accent = '2E75B6';
const green = '2E8B57';
const gray = '666666';
const lightBlue = 'D9EAF7';
const lightGreen = 'E2F0D9';
const lightGray = 'F2F2F2';
const border = { style: BorderStyle.SINGLE, size: 1, color: 'B7C9D6' };
const borders = { top: border, bottom: border, left: border, right: border };

const p = (text, options = {}) => new Paragraph({
  spacing: { after: options.after ?? 100, before: options.before ?? 0 },
  alignment: options.alignment,
  children: [new TextRun({
    text,
    bold: options.bold,
    italics: options.italics,
    color: options.color,
    font: 'Arial',
    size: options.size,
  })],
});

const heading = (text, level = HeadingLevel.HEADING_1) => new Paragraph({
  heading: level,
  children: [new TextRun({ text, font: 'Arial', bold: true, color: accent })],
});

const bullet = (text, level = 0) => new Paragraph({
  numbering: { reference: 'bullets', level },
  spacing: { after: 70 },
  children: [new TextRun({ text, font: 'Arial' })],
});

const codeBlock = (lines) => lines.map((line) => new Paragraph({
  shading: { fill: 'F5F7F9', type: ShadingType.CLEAR },
  spacing: { after: 0, before: 0, line: 240 },
  indent: { left: 180, right: 180 },
  children: [new TextRun({ text: line || ' ', font: 'Consolas', size: 18, color: '222222' })],
}));

const table = (headers, rows, widths) => new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: widths,
  rows: [
    new TableRow({ children: headers.map((text, i) => new TableCell({
      borders, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: lightBlue, type: ShadingType.CLEAR },
      margins: { top: 90, bottom: 90, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text, bold: true, font: 'Arial' })] })],
    })) }),
    ...rows.map((row, ri) => new TableRow({ children: row.map((text, i) => new TableCell({
      borders, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: ri % 2 ? 'FFFFFF' : lightGray, type: ShadingType.CLEAR },
      margins: { top: 90, bottom: 90, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: String(text), font: 'Arial', size: 20 })] })],
    })) })),
  ],
});

const children = [];
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 }, children: [new TextRun({ text: 'S5700 交换机 VLAN 10/20 实验报告', bold: true, font: 'Arial', size: 36, color: accent })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 }, children: [new TextRun({ text: 'VLAN 间隔离与三层网关配置', font: 'Arial', size: 24, color: green })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 360 }, children: [new TextRun({ text: '实验日期：2026年8月25日', font: 'Arial', size: 20, color: gray })] }));

children.push(heading('一、实验概述'));
children.push(p('本实验在 eNSP 中使用 1 台 S5700 三层交换机和 2 台 PC。PC1 接入交换机的 GigabitEthernet0/0/1，属于 VLAN 10；PC2 接入 GigabitEthernet0/0/2，属于 VLAN 20。'));
children.push(p('实验目标是在交换机上创建 VLAN、配置 Access 端口和 VLANIF 三层网关，并验证两个 VLAN 的终端均可通过本机网关通信。'));

children.push(heading('二、实验拓扑'));
children.push(table(['设备', '型号', '接口', 'VLAN', 'IP 地址', '网关'], [
  ['PC1', 'PC', 'Ethernet0/0/0 ↔ SW1 GE0/0/1', '10', '192.168.10.10/24', '192.168.10.1'],
  ['PC2', 'PC', 'Ethernet0/0/0 ↔ SW1 GE0/0/2', '20', '192.168.20.10/24', '192.168.20.1'],
  ['SW1', 'S5700', '三层交换机', 'VLANIF 10/20', '192.168.10.1/24；192.168.20.1/24', '—'],
], [1450, 1250, 2650, 1050, 1800, 826]));
children.push(p('拓扑链路：', { before: 160, bold: true, color: accent }));
children.push(...codeBlock(['PC1（192.168.10.10/24，网关 192.168.10.1）', '        │  Ethernet0/0/0 ↔ GigabitEthernet0/0/1  Access VLAN 10', '        ▼', '                         SW1', '                         ├── Vlanif10：192.168.10.1/24', '                         └── Vlanif20：192.168.20.1/24', '        ▲', '        │  Ethernet0/0/0 ↔ GigabitEthernet0/0/2  Access VLAN 20', 'PC2（192.168.20.10/24，网关 192.168.20.1）']));
children.push(p('拓扑来源：examples/topologies/vlan_lab.topo。S5700 的 .topo XML srcIndex 为 0-based，PC1、 PC2 分别使用 srcIndex=0/1，对应设备真实 GE0/0/1/2。', { before: 120, color: gray, size: 18 }));

children.push(heading('三、关键配置'));
children.push(p('以下命令由 grbj-ensp MCP 在 eNSP 的 SW1 会话上下发。交换机为 S5700，VRP 版本为 5.110。接口配置使用全称 GigabitEthernet0/0/1 和 GigabitEthernet0/0/2。'));
children.push(...codeBlock([
  'system-view',
  'vlan 10',
  'quit',
  'vlan 20',
  'quit',
  'interface GigabitEthernet 0/0/1',
  ' port link-type access',
  ' port default vlan 10',
  'quit',
  'interface GigabitEthernet 0/0/2',
  ' port link-type access',
  ' port default vlan 20',
  'quit',
  'interface Vlanif 10',
  ' ip address 192.168.10.1 255.255.255.0',
  'quit',
  'interface Vlanif 20',
  ' ip address 192.168.20.1 255.255.255.0',
  'quit',
  'save',
]));
children.push(p('保存结果：The current configuration was saved successfully.', { bold: true, color: green }));

children.push(heading('四、验证结果'));
children.push(heading('4.1 设备信息', HeadingLevel.HEADING_2));
children.push(table(['项目', '结果'], [
  ['设备型号', 'S5700'],
  ['VRP 软件版本', '5.110'],
  ['设备启动时间', '约 15 分钟'],
  ['Telnet 会话', 'SW1 / 127.0.0.1:2000，active'],
], [3000, 6026]));
children.push(heading('4.2 VLAN 与 Access 端口', HeadingLevel.HEADING_2));
children.push(table(['VLAN', 'Access 端口', '端口状态', '结果'], [
  ['VLAN 10', 'GigabitEthernet0/0/1', 'U，Access/untagged', '通过'],
  ['VLAN 20', 'GigabitEthernet0/0/2', 'U，Access/untagged', '通过'],
], [1700, 3000, 2200, 2126]));
children.push(heading('4.3 三层接口状态', HeadingLevel.HEADING_2));
children.push(table(['接口', 'IP 地址', 'Physical', 'Protocol', '结果'], [
  ['Vlanif10', '192.168.10.1/24', 'up', 'up', '通过'],
  ['Vlanif20', '192.168.20.1/24', 'up', 'up', '通过'],
], [1800, 2400, 1500, 1500, 1826]));
children.push(heading('4.4 连通性验证', HeadingLevel.HEADING_2));
children.push(table(['测试源', '测试目标', '发送/接收', '丢包率', '平均时延', '结果'], [
  ['SW1', '192.168.10.10（PC1）', '4 / 4', '0%', '约 67 ms', '通过'],
  ['SW1', '192.168.20.10（PC2）', '4 / 4', '0%', '约 50 ms', '通过'],
], [1500, 2200, 1400, 1000, 1400, 1526]));
children.push(p('验证结论：VLAN 10 与 VLAN 20 均已创建，两个 Access 端口均已正确划入 VLAN；两个 VLANIF 网关接口均为 up/up；SW1 对 PC1、PC2 的 4 次探测均收到回复，丢包率为 0%。', { bold: true, color: green }));

children.push(heading('五、故障排查建议'));
children.push(bullet('PC1 或 PC2 无法访问自身网关：先检查对应 PC 的 IP、掩码和网关，再检查 display vlan 中端口是否处于正确 VLAN。'));
children.push(bullet('display vlan 中端口仍出现在 VLAN 1：检查 interface GigabitEthernet 0/0/1/2 是否为 port link-type access，并确认 port default vlan 10/20 已执行。'));
children.push(bullet('Access 端口为 Up 但网关不通：检查 Vlanif10/Vlanif20 的地址、掩码和 up/up 状态；不要只看 display ip interface brief 的 Vlanif1。'));
children.push(bullet('两台 PC 均无法通信：使用 display interface brief 或 LLDP 邻居确认线缆和实际物理接口；S5700 的 .topo srcIndex=0/1 对应 GE0/0/1/2。'));
children.push(bullet('发现配置分页输出：先发送空格翻页，再继续下一条 display 或配置命令，避免后续命令被设备当作 More 的输入。'));
children.push(bullet('交换机 console 超时：5 分钟无操作后可能退回用户视图；重新下发前先用 display current-configuration 或 display clock 探查当前模式。'));

children.push(heading('六、实验结论'));
children.push(p('本次实验已成功完成 VLAN 10、VLAN 20、Access 端口、VLANIF 三层网关配置，并通过保存配置、display vlan、display ip interface brief 和两组 Ping 测试完成验证。实验结果满足预期。', { bold: true, color: green }));

const doc = new Document({
  styles: {
    default: { document: { run: { font: 'Arial', size: 22 } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { size: 28, bold: true, font: 'Arial', color: accent }, paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 0 } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { size: 24, bold: true, font: 'Arial', color: green }, paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [{ reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: 'S5700 VLAN 10/20 实验', font: 'Arial', size: 16, color: gray })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: '实验报告  |  第 ', font: 'Arial', size: 16, color: gray }), new TextRun({ children: [PageNumber.CURRENT], font: 'Arial', size: 16, color: gray }), new TextRun({ text: ' 页', font: 'Arial', size: 16, color: gray })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync(outputPath, buffer);
  console.log(outputPath);
});
