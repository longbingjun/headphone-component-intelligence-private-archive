"""52audio 情报源专用的可配置规则表（关键词/别名/优先级）。

这个文件是"数据"，不是"逻辑"：所有真正的判断算法都在 core/extract/ 或
sources/audio52/source.py 里，这里只放规则，方便日后运营/调参时直接改这个文件，
不需要碰爬虫和抽取逻辑代码。

规则表都是根据 2026-07 抓取到的 52audio「拆解」分类真实文章标题/正文调研总结出来的，
后续如果发现新品类/新品牌，直接在对应列表里追加即可。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. 产品分类体系（有序规则，按列表顺序从上到下第一个命中的就是最终分类，
#    所以"更具体"的分类要排在"更宽泛"的分类前面，例如"开放式耳机"要排在
#    "真无线耳机TWS"前面，否则"开放式真无线耳机"会被先归到 TWS）。
# ---------------------------------------------------------------------------
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("骨传导耳机", ["骨传导"]),
    ("开放式耳机", ["开放式", "耳夹式", "耳夹", "挂耳式", "耳挂式"]),
    ("颈挂式蓝牙耳机", ["颈挂式", "颈挂", "颈戴式", "颈戴"]),
    ("头戴式耳机", ["头戴式", "头戴"]),
    ("真无线耳机TWS", ["真无线", "TWS", "tws"]),
    ("有线耳机", ["有线耳机", "入耳式耳机", "入耳式"]),
    ("智能手表", ["手表", "Watch", "watch"]),
    ("AI眼镜及穿戴设备", ["AI眼镜", "眼镜", "Vision", "VR", "vision"]),
    ("音箱及其他音频设备", ["音箱", "音响", "Speaker", "speaker", "麦克风", "Mic", "录音"]),
]
DEFAULT_CATEGORY = "其他音频设备"

# ---------------------------------------------------------------------------
# 2. 品牌别名表：(展示名, [标题里可能出现的各种写法])
#    匹配时优先匹配别名字符串更长的项，避免"Bowie"先命中导致"Baseus倍思"漏掉之类的问题。
# ---------------------------------------------------------------------------
BRAND_ALIASES: list[tuple[str, list[str]]] = [
    # 近期拆解标题中反复出现、且可由品牌名唯一判定的品牌。保留中英文写法，
    # 让历史文章在重建产品索引时也能回填到稳定的 canonical ID。
    ("LIBRATONE小鸟", ["LIBRATONE小鸟", "Libratone小鸟", "LIBRATONE", "Libratone", "小鸟音响"]),
    ("Cleer", ["Cleer"]),
    ("BEEBEST极蜂", ["BEEBEST极蜂", "BEEBEST", "极蜂"]),
    ("CHERRY樱桃", ["CHERRY樱桃", "CHERRY", "樱桃"]),
    ("Dangbei当贝", ["Dangbei当贝", "Dangbei", "当贝"]),
    ("MARTUBE马克图布", ["MARTUBE马克图布", "MARTUBE", "马克图布"]),
    ("MORROR ART", ["MORROR ART", "MORROR"]),
    ("Keep", ["Keep"]),
    ("MOVA", ["MOVA"]),
    ("UMELODY悠律", ["UMELODY悠律", "UMELODY", "悠律"]),
    ("OpenRock开石", ["OpenRock开石", "OpenRock", "开石"]),
    ("LINKLIKE莅莱", ["LINKLIKE莅莱", "LINKLIKE", "莅莱"]),
    ("MONSTER魔声", ["MONSTER魔声", "Monster魔声", "MONSTER", "Monster", "魔声"]),
    ("Logitech罗技", ["Logitech罗技", "Logitech", "罗技"]),
    ("EarFun丽耳", ["EarFun", "earfün", "丽耳"]),
    ("JSOUL眷蜀", ["JSOUL眷蜀", "JSOUL", "眷蜀"]),
    ("iTour", ["iTour", "亲爱的翻译官"]),
    ("MACHENIKE机械师", ["MACHENIKE机械师", "MACHENIKE", "机械师"]),
    ("MOJAWA墨觉", ["MOJAWA墨觉", "MOJAWA", "墨觉"]),
    ("Newmine纽曼", ["Newmine纽曼", "Newmine", "纽曼"]),
    ("HAKII哈氪", ["HAKII", "哈氪"]),
    ("TRÜKE充客", ["TRÜKE充客", "TRÜKE", "Trüke", "充客"]),
    ("XIBERIA西伯利亚", ["XIBERIA西伯利亚", "XIBERIA", "西伯利亚"]),
    ("Oladance", ["Oladance"]),
    ("iKF", ["iKF"]),
    ("SOAIY索爱", ["SOAIY索爱", "SOAIY", "索爱"]),
    ("LANGSDOM兰士顿", ["LANGSDOM兰士顿", "LANGSDOM", "兰士顿"]),
    ("MOMA猛玛", ["MOMA猛玛", "MOMA", "猛玛"]),
    ("Marshall马歇尔", ["Marshall马歇尔", "Marshall", "马歇尔"]),
    ("Jabra捷波朗", ["Jabra捷波朗", "Jabra", "捷波朗"]),
    ("DENON天龙", ["DENON天龙", "DENON", "天龙"]),
    ("MEIZU魅族", ["MEIZU魅族", "MEIZU", "魅族"]),
    ("Redmi红米", ["Redmi红米", "Redmi", "红米"]),
    ("Apple苹果", ["Apple苹果", "Apple", "苹果"]),
    ("Beats", ["Beats"]),
    ("BOSE", ["BOSE", "Bose"]),
    ("OPPO", ["OPPO"]),
    ("OnePlus一加", ["OnePlus一加", "OnePlus", "一加"]),
    ("1MORE万魔", ["1MORE万魔", "1MORE", "万魔"]),
    ("UGREEN绿联", ["UGREEN绿联", "UGREEN", "绿联"]),
    ("SoundPEATS泥炭", ["SoundPEATS泥炭", "SOUNDPEATS泥炭", "SoundPEATS", "SOUNDPEATS", "泥炭"]),
    ("FIIL", ["FIIL"]),
    ("FiiO飞傲", ["FiiO飞傲", "FiiO", "飞傲"]),
    ("Technics松下", ["Technics松下", "Technics", "松下"]),
    ("Lenovo联想", ["Lenovo联想", "Lenovo", "联想"]),
    ("Motorola摩托罗拉", ["Motorola", "摩托罗拉", "moto"]),
    ("HIFIMAN海菲曼", ["HIFIMAN海菲曼", "HIFIMAN", "海菲曼"]),
    ("SENNHEISER森海塞尔", ["SENNHEISER森海塞尔", "SENNHEISER", "森海塞尔"]),
    ("Audio-Technica铁三角", ["Audio-Technica", "铁三角"]),
    ("Bowers & Wilkins宝华韦健", ["Bowers & Wilkins", "宝华韦健"]),
    ("beyerdynamic拜雅", ["beyerdynamic", "拜雅"]),
    ("JLab", ["JLab"]),
    ("TOZO", ["TOZO"]),
    ("iQOO", ["iQOO"]),
    ("iFLYTEK科大讯飞", ["iFLYTEK科大讯飞", "iFLYTEK", "科大讯飞"]),
    ("Timekettle时空壶", ["Timekettle时空壶", "Timekettle", "时空壶"]),
    ("NANK南卡", ["NANK南卡", "NANK", "南卡"]),
    ("Haylou", ["Haylou"]),
    ("acer宏碁", ["acer宏碁", "acer", "宏碁"]),
    ("Razer雷蛇", ["Razer雷蛇", "Razer", "雷蛇"]),
    ("YAMAHA雅马哈", ["YAMAHA雅马哈", "YAMAHA", "雅马哈"]),
    ("KEF", ["KEF"]),
    ("Baseus倍思", ["Baseus倍思", "Baseus", "倍思"]),
    ("荣耀", ["荣耀亲选", "荣耀", "HONOR"]),
    ("JBL", ["JBL"]),
    ("kaiboaudio凯博", ["kaiboaudio", "KAIBO", "凯博"]),
    ("Xiaomi小米", ["Xiaomi小米", "Xiaomi", "小米"]),
    ("DPVR大朋", ["DPVR大朋", "DPVR", "大朋"]),
    ("SONY索尼", ["SONY索尼", "SONY", "索尼"]),
    ("天猫精灵", ["TMALL GENIE天猫精灵", "TMALL GENIE", "天猫精灵"]),
    ("SHOKZ韶音", ["SHOKZ韶音", "SHOKZ", "韶音"]),
    ("PHILIPS飞利浦", ["PHILIPS飞利浦", "PHILIPS", "飞利浦"]),
    ("Soundcore声阔", ["Soundcore声阔", "Soundcore", "声阔"]),
    ("realme真我", ["realme真我", "realme", "真我"]),
    ("ROSESELSA弱水时砂", ["ROSESELSA弱水时砂", "ROSESELSA", "弱水时砂"]),
    ("EDIFIER漫步者", ["EDIFIER漫步者", "EDIFIER", "漫步者"]),
    ("MOONDROP水月雨", ["MOONDROP水月雨", "MOONDROP", "水月雨"]),
    ("OKSJ欧克士", ["OKSJ欧克士", "OKSJ", "欧克士"]),
    ("SoundAI声智", ["SoundAI声智", "SoundAI", "声智"]),
    ("Nothing", ["Nothing"]),
    ("迪士尼", ["迪士尼系列", "迪士尼", "Disney"]),
    ("QCY意象", ["QCY意象", "QCY", "意象"]),
    ("JOYROOM机乐堂", ["JOYROOM机乐堂", "JOYROOM", "机乐堂"]),
    ("半至", ["半至"]),
    ("COLORFLY七彩虹", ["COLORFLY七彩虹", "COLORFLY", "七彩虹"]),
    ("PICKFUN皮克方", ["PICKFUN皮克方", "PICKFUN", "皮克方"]),
    ("HUAWEI华为", ["HUAWEI华为", "HUAWEI", "华为"]),
    ("大疆DJI", ["大疆DJI", "DJI", "大疆"]),
    ("vivo", ["vivo"]),
    ("YOBYBO", ["YOBYBO"]),
    ("SANAG塞那", ["SANAG塞那", "SANAG", "塞那"]),
    ("BUTTONS", ["BUTTONS"]),
    ("理想", ["理想AI智能眼镜", "理想"]),
    ("JVC", ["JVC"]),
    ("Anker", ["Anker"]),
    ("华米Amazfit", ["华米Amazfit", "Amazfit", "华米"]),
]

# 拼在标题末尾、用来判断分类的"产品形态后缀词"，从"标题去掉品牌名"之后的剩余
# 文本里，把这些后缀词切掉即可近似得到"型号"部分（启发式，非 100% 精确）。
PRODUCT_TYPE_SUFFIXES = [
    "真无线降噪耳机", "真无线蓝牙耳机", "真无线耳机", "无线降噪耳机", "无线蓝牙耳机",
    "开放式耳机", "耳夹式真无线耳机", "耳夹耳机", "挂耳式智能音响",
    "头戴式降噪耳机", "头戴式耳机", "骨传导耳机", "入耳式耳机", "有线耳机",
    "颈挂式蓝牙耳机", "颈挂式耳机", "智能手表", "AI眼镜", "AI智能眼镜",
    "智能音箱", "装饰音箱", "便携智能音箱", "音箱", "音响",
    "无线麦克风", "录音卡", "监控伴侣", "无线耳机组", "耳机", "眼镜", "手表",
]

# ---------------------------------------------------------------------------
# 3. 卖点/特色关键词库：句子命中这些词，就更可能是品牌想强调的卖点/特色描述。
# ---------------------------------------------------------------------------
SELLING_POINT_KEYWORDS = [
    "首发", "首创", "独家", "专利", "全球首", "业内首", "突破", "领先", "旗舰",
    "升级", "全新", "创新", "定制", "高端", "顶级", "极致", "超长续航", "超长",
    "大幅提升", "显著提升", "行业领先", "第一", "认证", "小金标", "Hi-Res",
    "空间音效", "降噪", "防水", "防尘", "快充", "低延迟",
    "沉浸式", "舒适佩戴", "轻量化", "高保真", "臻品音质", "黄金振膜", "石墨烯",
    "钛振膜", "生物振膜", "大动圈", "圈铁", "三频", "解析力", "灵敏度",
    "开放佩戴", "定向传音", "空间音频", "游戏模式", "通话降噪", "风噪",
]

# 卖点标签枚举：抽取 selling_points 时附带 tag（可多标签取最高优先级）
SELLING_POINT_TAGS: list[tuple[str, list[str]]] = [
    ("降噪", ["降噪", "ANC", "主动降噪", "ENC", "通话降噪", "风噪消除"]),
    ("开放佩戴", ["开放佩戴", "开放式", "定向传音", "漏音控制", "耳夹", "挂耳"]),
    ("空间音频", ["空间音频", "空间音效", "空间声场", "头部追踪", "3D音效"]),
    ("长续航", ["超长续航", "长续航", "续航时间", "综合续航", "快充"]),
    ("游戏低延迟", ["低延迟", "游戏模式", "游戏低延迟", "电竞模式"]),
    ("音质认证", ["Hi-Res", "小金标", "臻品音质", "LDAC", "高解析"]),
    ("舒适佩戴", ["舒适佩戴", "轻量化", "人体工学", "稳固佩戴"]),
    ("防水防尘", ["防水", "防尘", "IPX", "IP5", "IP6", "IP67", "IP68"]),
    ("旗舰定位", ["旗舰", "高端", "顶级", "奢华", "首发", "首创", "独家"]),
]

# 技术/芯片句特征词（应从 A 区卖点归位到 B/D/E）
TECH_SENTENCE_KEYWORDS = [
    "芯片", "SoC", "主控", "蓝牙", "Bluetooth", "LDAC", "aptX", "LHDC", "AAC", "SBC",
    "QCC", "AB15", "BES", "恒玄", "络达", "高通", "Qualcomm", "Actions", "JL",
    "固件", "OTA", "多点连接", "一拖二", "蓝牙5", "蓝牙 5", "编解码",
]

# structure 内部结构描述中应过滤的包装盒/配件句
STRUCTURE_EXCLUDE_KEYWORDS = ["包装盒", "配件一览", "说明书", "外包装", "配件物品", "产品手册", "包装背面", "包装正面"]

FASTENER_KEYWORDS = ["螺丝", "螺钉", "卡扣", "卡榫", "卡槽", "胶粘", "胶水", "超声焊接", "热熔", "卡接", "固定卡"]

SEALING_KEYWORDS = ["密封圈", "O型圈", "防水胶", "点胶", "超声熔接", "防水涂层", "密封胶", "防尘网"]

SUPPLY_HINT_KEYWORDS = ["供应商", "代工", "模组厂", "原厂", "采购", "供应链", "方案商", "器件商"]

KEY_IMAGE_KEYWORDS: dict[str, list[str]] = {
    "pcb": ["主板", "PCBA", "电路板", "芯片", "主控", "蓝牙芯片", "电源管理"],
    "battery": ["电池", "电芯", "mAh", "锂电"],
    "exploded": ["爆炸图", "分解", "拆解", "内部结构", "一览", "全家福"],
    "packaging": ["包装", "包装盒", "开箱"],
    "overview": ["外观", "正面", "背面", "侧面", "整机", "耳机外观"],
}

EARBUD_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("入耳", ["入耳式", "入耳", "TWS", "真无线"]),
    ("开放", ["开放式", "开放佩戴", "OWS", "气传导"]),
    ("耳夹", ["耳夹", "耳夹式", "夹耳"]),
    ("头戴", ["头戴", "头梁", "耳罩"]),
    ("颈挂", ["颈挂", "颈戴", "挂脖"]),
]

# ---------------------------------------------------------------------------
# 4. 部件关键词库：{展示名: {"importance": "major"/"minor", "aliases": [...]}}。
#    major = 通常是拆解报告重点分析的核心部件；minor = 结构性/外围部件。
# ---------------------------------------------------------------------------
COMPONENT_LEXICON: dict[str, dict] = {
    "喇叭单元": {"importance": "major", "aliases": ["喇叭单元", "动圈单元", "动铁单元", "扬声器单元", "发声单元", "振膜"]},
    "主板/PCBA": {"importance": "major", "aliases": ["主板", "PCBA", "电路板", "主控芯片", "蓝牙芯片", "SoC"]},
    "电池": {"importance": "major", "aliases": ["电池", "电芯", "锂电池", "聚合物电池"]},
    "充电仓": {"importance": "major", "aliases": ["充电仓", "充电盒", "充电舱"]},
    "天线": {"importance": "major", "aliases": ["天线"]},
    "麦克风": {"importance": "major", "aliases": ["麦克风", "MIC", "拾音"]},
    "降噪系统": {"importance": "major", "aliases": ["降噪芯片", "ANC", "主动降噪", "ENC"]},
    "骨传导振子": {"importance": "major", "aliases": ["骨传导振子", "振子"]},
    "耳塞套": {"importance": "minor", "aliases": ["耳塞套", "耳帽", "硅胶套", "耳翼"]},
    "耳挂/挂钩": {"importance": "minor", "aliases": ["耳挂", "挂钩", "耳钩", "鼻梁架", "镜腿"]},
    "触控/按键": {"importance": "minor", "aliases": ["触控", "按键", "按钮", "触摸板"]},
    "指示灯": {"importance": "minor", "aliases": ["指示灯", "LED灯"]},
    "充电触点": {"importance": "minor", "aliases": ["充电触点", "Pogo Pin", "弹簧针"]},
    "包装盒": {"importance": "minor", "aliases": ["包装盒", "包装设计", "外包装"]},
    "说明书": {"importance": "minor", "aliases": ["说明书", "使用手册"]},
    "外壳结构": {"importance": "minor", "aliases": ["外壳", "中框", "后盖", "前腔", "后腔"]},
}

# ---------------------------------------------------------------------------
# 5. 技术参数抽取规则：{字段名: [命中关键词]}
# ---------------------------------------------------------------------------
TECH_SPEC_RULES: dict[str, list[str]] = {
    "charging_method": ["充电方式", "无线充电", "有线充电", "磁吸充电", "跟随充电"],
    "charging_port": ["充电接口", "Type-C", "Type C", "Micro USB", "Lightning"],
    "manual_notes": ["说明书", "使用说明", "警示", "使用手册"],
    "product_markings": ["额定输入", "额定电压", "型号：", "型号:", "认证", "3C", "CE", "FCC", "频响范围", "防护等级", "IP"],
}

# ---------------------------------------------------------------------------
# 6. v2 角色视图扩展关键词
# ---------------------------------------------------------------------------
SCENARIO_KEYWORDS = ["运动", "办公", "游戏", "通勤", "睡眠", "通话", "跑步", "健身", "户外", "电竞"]

MATERIAL_KEYWORDS = ["硅胶", "钛合金", "镁合金", "铝合金", "塑料", "PC", "ABS", "不锈钢", "尼龙", "皮革", "液态硅胶"]

CODEC_KEYWORDS = ["LDAC", "AAC", "aptX", "LHDC", "SBC", "LC3", "Hi-Res", "小金标"]

CHIP_PATTERNS = [
    r"Qualcomm\s+[A-Z0-9\-]+",
    r"高通\s*[A-Z0-9\-]+",
    r"Actions\s+[A-Z0-9]+",
    r"络达\s*[A-Z0-9\-]+",
    r"恒玄\s*[A-Z0-9\-]+",
    r"BES\s*[A-Z0-9\-]+",
    r"JL\s*杰理科技\s*AC\d+[A-Z0-9]*",
    r"杰理科技\s*AC\d+[A-Z0-9]*",
    r"JL\d+",
    r"AB\d+",
    r"QCC\d+",
    # V3 Phase1：总结段常见国产芯片型号（按 281175/280166 实测扩展）
    r"INJOINIC\s*英集芯\s*IP\d+[A-Z]?",
    r"英集芯\s*IP\d+[A-Z]?",
    r"IP5528",
    r"IP5526",
    r"IP5516",
    r"Bluetrum\s*中科蓝讯\s*BT\d+[A-Z]?",
    r"中科蓝讯\s*BT\d+[A-Z]?",
    r"BT8912F",
    r"BT\d{4}[A-Z]",
    r"BES2700iH",
    r"BES\d{4}[a-zA-Z]*",
    r"思远半导体\s*SY\d+",
    r"SY8805",
    r"SY\d{4}",
    r"ConvenientPower\s*易冲半导体\s*CPS\d+",
    r"易冲半导体\s*CPS\d+",
    r"CPS4520",
    r"CPS\d{4}",
    r"XHSC\s*小华半导体\s*HC\d+[A-Z][A-Z0-9]+",
    r"小华半导体\s*HC\d+[A-Z][A-Z0-9]+",
    r"HC32L170FAUA",
    r"HC32L\d{3}[A-Z]+",
    r"WINSEMI\s*稳先微\s*WSDF\d+[A-Z]?",
    r"稳先微\s*WSDF\d+[A-Z]?",
    r"WSDF5317C",
    r"WSDF\d{4}[A-Z]",
    r"Prisemi\s*芯导\s*P\d+[A-Z]+\d?[A-Z]?",
    r"芯导\s*P\d+[A-Z]+\d?[A-Z]?",
    r"P14C1S",
    r"晶晨\s*A113X",
    r"A113X",
    r"AIC8800",
    r"爱科微\s*AIC\d+",
    r"昆腾\s*KT\d+[A-Z]?",
    r"KT0656M",
    r"KT\d{4}[A-Z]?",
    # 拆解视频字幕和 OCR 常见型号（含简繁变体）
    r"SS-?88[01]",
    r"ETA\d{4}[A-Z]?",
    r"CM\d{4}[A-Z]?",
    r"IP\d{4}[A-Z]?",
    r"WP-?\d{4}[A-Z]?",
    r"BES\d{3,}[A-Z0-9]*",
    r"CSU\d{2}[A-Z0-9]+",
    r"SY\d{4}[A-Z]?",
]

# 芯片型号 → 厂商展示名（用于 BOM 行 brand 字段归一化）。
# 优先按「品牌前缀+型号」匹配；命中后给 BOM 行打 brand 标签。
CHIP_BRAND_MAP: list[tuple[str, str]] = [
    ("INJOINIC", "INJOINIC英集芯"),
    ("英集芯", "INJOINIC英集芯"),
    ("IP5528", "INJOINIC英集芯"),
    ("IP5526", "INJOINIC英集芯"),
    ("IP5516", "INJOINIC英集芯"),
    ("Bluetrum", "Bluetrum中科蓝讯"),
    ("中科蓝讯", "Bluetrum中科蓝讯"),
    ("BT8912F", "Bluetrum中科蓝讯"),
    ("BES", "BES恒玄科技"),
    ("恒玄", "BES恒玄科技"),
    ("BES2700", "BES恒玄科技"),
    ("SY", "思远半导体"),
    ("思远", "思远半导体"),
    ("CPS", "ConvenientPower易冲半导体"),
    ("易冲", "ConvenientPower易冲半导体"),
    ("HC32L", "XHSC小华半导体"),
    ("小华半导体", "XHSC小华半导体"),
    ("WSDF", "WINSEMI稳先微"),
    ("稳先微", "WINSEMI稳先微"),
    ("P14C1S", "Prisemi芯导"),
    ("芯导", "Prisemi芯导"),
    ("A113X", "晶晨"),
    ("晶晨", "晶晨"),
    ("AIC8800", "爱科微"),
    ("爱科微", "爱科微"),
    ("KT0656M", "昆腾"),
    ("昆腾", "昆腾"),
    ("Qualcomm", "Qualcomm高通"),
    ("高通", "Qualcomm高通"),
    ("QCC", "Qualcomm高通"),
    ("Actions", "Actions"),
    ("络达", "Airoha络达"),
    ("JL", "JL杰理"),
]
