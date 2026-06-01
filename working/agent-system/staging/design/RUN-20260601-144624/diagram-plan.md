# Diagram Plan

- Run ID: `RUN-20260601-144624`
- Generated At: `2026-06-01T14:46:25+08:00`

| Diagram | Title | Kind | Layout | Sections | Source IDs | Purpose |
|---|---|---|---|---|---|---|
| DG001 | 总体架构蓝图 | architecture | flowchart TB | SEC002, SEC003, SEC004, SEC006, SEC010, SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034, SEC016, SEC027, SEC028, SEC029, SEC030 | T029, T030, T031, Q013, Q002, Q005, Q010, T034, Q001, Q007, Q009, T035, T036, T037 | 展示用户交互、业务功能、数据接口、平台运行和质量安全保障之间的分层关系。 |
| DG002 | 功能模块划分图 | architecture | flowchart TB | SEC007, SEC008, SEC001, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034, SEC016, SEC027, SEC028, SEC029, SEC030, SEC002, SEC003, SEC004, SEC006, SEC017, SEC010, SEC005, SEC012, SEC013, SEC014, SEC019, SEC026, SEC033 | T001, T002, T003, T004, T005, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T026, T027, T028, T029, T030, T031, T032, T033, T034, Q001, Q007, T035, T036, T037, T038 | 展示海图显示、态势融合、航线规划、告警监控、个性化配置和数据维护等功能模块边界。 |
| DG003 | 核心业务流程图 | business_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034, SEC016, SEC027, SEC028, SEC029, SEC030, SEC005, SEC010, SEC012, SEC013, SEC014, SEC019, SEC026, SEC033 | T001, T002, T003, T004, T005, T006, T007, T008, T009, T010, T011, T012, T013, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T028, T032, Q001, Q007, T035, T036, T037 | 说明电子海图加载、目标信息融合、航线规划、航行监控和告警处置的主业务路径。 |
| DG004 | 数据流与接口关系图 | data_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030, SEC002, SEC003, SEC004, SEC010, SEC034, SEC016, SEC009, SEC005, SEC012, SEC013, SEC014, SEC019, SEC015, SEC017, SEC026, SEC033, SEC031, SEC032 | T001, T005, T021, T023, T031, T032, T033, T034, P001, P003, P004, Q001, Q004, Q007, Q009, T035, T036, T037, T038, T039, T040, T041 | 说明外部导航设备、文件系统、数据库读写、HMI 控制和业务模块之间的数据流向。 |
| DG005 | 部署与运行支撑图 | deployment | flowchart TB | SEC009, SEC001, SEC002, SEC003, SEC004, SEC006, SEC007, SEC008, SEC017, SEC018, SEC023, SEC024, SEC025, SEC013, SEC026, SEC030, SEC033, SEC034, SEC031, SEC005, SEC010, SEC011, SEC012, SEC014, SEC016, SEC019, SEC027, SEC028, SEC029 | P001, P002, P003, P004, P005, P006, P007, T029, Q001, Q002, Q005, Q010 | 说明国产化软硬件环境、跨平台运行、多设备协同和性能保障的部署关系。 |
| DG006 | 安全与质量保障流程图 | security | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030, SEC011, SEC024, SEC025, SEC034, SEC005, SEC010, SEC012, SEC013, SEC014, SEC019, SEC006, SEC017, SEC015, SEC026, SEC033, SEC031, SEC032, SEC002, SEC003, SEC004 | T011, T012, T016, T017, T018, T020, T028, T033, Q001, Q002, Q003, Q004, Q005, Q006, Q007, Q008, Q009, Q010, T035, Q011, Q012, Q013 | 说明权限、保密、质量监督、测试验收和风险处置的闭环控制。 |
| DG007 | T001 海图综合态势展示-标准符合性功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025 | T001 | 说明海图综合态势展示-标准符合性的输入、处理、输出与异常记录流程。 |
| DG008 | T002 海图综合态势展示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025 | T002 | 说明海图综合态势展示的输入、处理、输出与异常记录流程。 |
| DG009 | T003 海图综合态势展示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025 | T003 | 说明海图综合态势展示的输入、处理、输出与异常记录流程。 |
| DG010 | T004 海图综合态势展示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025 | T004 | 说明海图综合态势展示的输入、处理、输出与异常记录流程。 |
| DG011 | T005 AIS/雷达等目标信息的处理与显示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025 | T005 | 说明AIS/雷达等目标信息的处理与显示的输入、处理、输出与异常记录流程。 |
| DG012 | T006 AIS/雷达等目标信息的处理与显示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034 | T006 | 说明AIS/雷达等目标信息的处理与显示的输入、处理、输出与异常记录流程。 |
| DG013 | T007 AIS/雷达等目标信息的处理与显示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC034 | T007 | 说明AIS/雷达等目标信息的处理与显示的输入、处理、输出与异常记录流程。 |
| DG014 | T008 AIS/雷达等目标信息的处理与显示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023 | T008 | 说明AIS/雷达等目标信息的处理与显示的输入、处理、输出与异常记录流程。 |
| DG015 | T009 AIS/雷达等目标信息的处理与显示功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023 | T009 | 说明AIS/雷达等目标信息的处理与显示的输入、处理、输出与异常记录流程。 |
| DG016 | T010 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T010 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG017 | T011 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T011 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG018 | T012 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T012 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG019 | T013 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T013 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG020 | T014 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T014 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG021 | T015 航线规划功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T015 | 说明航线规划的输入、处理、输出与异常记录流程。 |
| DG022 | T016 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T016 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG023 | T017 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC016, SEC018, SEC023, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030 | T017 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG024 | T018 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T018 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG025 | T019 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T019 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG026 | T020 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T020 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG027 | T021 安全等深线与水深告警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030 | T021 | 说明安全等深线与水深告警的输入、处理、输出与异常记录流程。 |
| DG028 | T022 航行监控导航与安全预警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T022 | 说明航行监控导航与安全预警的输入、处理、输出与异常记录流程。 |
| DG029 | T023 航行监控导航与安全预警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030 | T023 | 说明航行监控导航与安全预警的输入、处理、输出与异常记录流程。 |
| DG030 | T024 航行监控导航与安全预警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T024 | 说明航行监控导航与安全预警的输入、处理、输出与异常记录流程。 |
| DG031 | T025 航行监控导航与安全预警功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T025 | 说明航行监控导航与安全预警的输入、处理、输出与异常记录流程。 |
| DG032 | T026 个性化设置需求功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T026 | 说明个性化设置需求的输入、处理、输出与异常记录流程。 |
| DG033 | T027 个性化设置需求功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC018, SEC023 | T027 | 说明个性化设置需求的输入、处理、输出与异常记录流程。 |
| DG034 | T028 个性化设置需求功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC016, SEC018, SEC023, SEC027, SEC028, SEC029, SEC030 | T028 | 说明个性化设置需求的输入、处理、输出与异常记录流程。 |
| DG035 | T029 基础平台与架构要求-跨平台兼容性功能流程图 | function_flow | flowchart TB | SEC001, SEC002, SEC003, SEC004, SEC006, SEC007, SEC008, SEC017, SEC018, SEC023 | T029 | 说明基础平台与架构要求-跨平台兼容性的输入、处理、输出与异常记录流程。 |
| DG036 | T030 基础平台与架构要求-多设备协同功能流程图 | function_flow | flowchart TB | SEC001, SEC002, SEC003, SEC004, SEC007, SEC008, SEC018, SEC023 | T030 | 说明基础平台与架构要求-多设备协同的输入、处理、输出与异常记录流程。 |
| DG037 | T031 基础平台与架构要求功能流程图 | function_flow | flowchart TB | SEC001, SEC002, SEC003, SEC004, SEC007, SEC008, SEC010, SEC011, SEC018, SEC023, SEC024, SEC025 | T031 | 说明基础平台与架构要求的输入、处理、输出与异常记录流程。 |
| DG038 | T032 数据维护与合规功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034 | T032 | 说明数据维护与合规的输入、处理、输出与异常记录流程。 |
| DG039 | T033 数据维护与合规功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC011, SEC016, SEC018, SEC023, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030, SEC034 | T033 | 说明数据维护与合规的输入、处理、输出与异常记录流程。 |
| DG040 | T034 数据维护与合规功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC023, SEC024, SEC025, SEC034 | T034 | 说明数据维护与合规的输入、处理、输出与异常记录流程。 |
| DG041 | T035 内部接口-安全告警与风险接口功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC016, SEC018, SEC024, SEC025, SEC027, SEC028, SEC029, SEC030 | T035 | 说明内部接口-安全告警与风险接口的输入、处理、输出与异常记录流程。 |
| DG042 | T036 内部接口-航线规划与监控接口功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T036 | 说明内部接口-航线规划与监控接口的输入、处理、输出与异常记录流程。 |
| DG043 | T037 内部接口-数据库数据读写接口功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T037 | 说明内部接口-数据库数据读写接口的输入、处理、输出与异常记录流程。 |
| DG044 | T038 内部接口-海图显示与态势融合接口功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T038 | 说明内部接口-海图显示与态势融合接口的输入、处理、输出与异常记录流程。 |
| DG045 | T039 外部接口-外部数据与文件系统功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T039 | 说明外部接口-外部数据与文件系统的输入、处理、输出与异常记录流程。 |
| DG046 | T040 外部接口-传感器与导航设备功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T040 | 说明外部接口-传感器与导航设备的输入、处理、输出与异常记录流程。 |
| DG047 | T041 外部接口-人机交互与控制HMI功能流程图 | function_flow | flowchart TB | SEC001, SEC007, SEC008, SEC010, SEC011, SEC018, SEC024, SEC025 | T041 | 说明外部接口-人机交互与控制HMI的输入、处理、输出与异常记录流程。 |
