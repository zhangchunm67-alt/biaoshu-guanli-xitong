"""Add editable evidence areas only when a submission form calls for them."""
import re


def added(kind, ref, **fields):
    return {"kind": kind, "source_index": ref, "evidence_slot": True, **fields}


def copied_nodes(blocks, src):
    return [node for block in blocks if block["kind"] == "copy"
            for node in src.nodes[block["start"]:block.get("end", block["start"]) + 1]]


def identity_frames(section, src):
    """Retain official printed labels and place the paste area immediately below each label."""
    result = []
    for i, block in enumerate(section):
        result.append(block)
        if block["kind"] != "copy" or block.get("end", block["start"]) != block["start"]:
            continue
        node = src.nodes[block["start"]]
        s = re.sub(r"\s+", "", node.text)
        role = r"(?:法定代表人|法人代表|授权代表|被授权人|委托代理人|授权代理人)"
        caption = re.fullmatch(r"(?:附[：:]?)?" + role + r"(?:(?:和|、|及)" + role + r")?(?:的)?"
                               r"(?:身份证(?:[/／或]护照)?复印件(?:[（(]正反面[）)])?|身份证明)[。；;]?", s)
        if not caption or node.graphics or (s.endswith("身份证明") and not s.startswith("附")):
            continue
        next_block = section[i+1:i+2]
        next_source = copied_nodes(next_block, src)
        if any(b["kind"] == "table" for b in next_block) or any(n.rows or n.graphics for n in next_source):
            continue
        result.append(added("table", node.index, rows=[["", ""]], min_height=1500, repeat_header=False))
    return result


def staff_forms(section, src):
    nodes = copied_nodes(section, src)
    proof = next((n for n in nodes if not n.rows and re.search(r"(?:须提供|需提供|附[：:]|附.{0,9}证明).{0,30}(?:资质|证书|身份证|学历|社保)", n.text)), None)
    roster = next((n for n in nodes if n.rows and "姓名" in n.rows[0] and not any(v == "" for v in n.rows[0])
                   and re.search(r"职务|承担|职责|参与阶段", "".join(n.rows[0]))), None)
    if not proof or not roster:
        return
    resume = next((n for n in nodes if n.rows and "姓名" in n.rows[0] and "" in n.rows[0]), None)
    ref = resume.index if resume else roster.index
    if not resume and not any(b["kind"] == "table" for b in section):
        wanted = ["姓名", "年龄", "职务", "职责", "责任", "工作年限", "职称", "学历", "工作内容", "经验"]
        labels = [s for s in roster.rows[0] if any(key in s for key in wanted)]
        if len(labels) > 1:
            section.append(added("text", ref, text="人员履历及证明（按实际人员复制）"))
            if len(labels) % 2:
                labels.append("")
            section.append(added("table", ref, repeat_header=False,
                                 rows=[[a, "", b, ""] for a, b in zip(labels[::2], labels[1::2])]))
    section.append(added("slot", proof.index, text="【待附】按本节原表和附注，为对应人员附证明。履历及证明按实际人数复制。"))


def identity_options(src):
    # A consecutive list of supplier types is one mutually conditional set.
    clusters, cluster = [], []
    for n in src.nodes:
        if n.tag == "p" and re.match(r"供应商(?:是|为|系|属于)", n.text) and re.search("应提供|须提供|需提供", n.text):
            cluster.append(n)
        elif n.text and cluster:
            clusters.append(cluster)
            cluster = []
    return next((c for c in clusters + [cluster] if len(c) >= 2), [])


def enrich(plan_dict, src):
    """Enrich a generated plan in-place. No files, reference frameworks or actual bidder data needed."""
    blocks = plan_dict["blocks"]
    if any(b.get("evidence_slot") for b in blocks):
        return
    options = identity_options(src)
    copied = {n.index for n in copied_nodes(blocks, src)}
    headings = [i for i, b in enumerate(blocks) if b["kind"] == "heading"]
    for pos, stop in reversed(list(zip(headings, headings[1:] + [len(blocks)]))):
        h = blocks[pos]
        title = h["text"]
        ref = h.get("source_index")
        if ref is None or not 0 <= ref < len(src.nodes):
            continue
        section = blocks[pos+1:stop]
        if re.search(r"身份证明|授权委托书|代表授权书|代表人授权书", title):
            section = identity_frames(section, src)
        if re.search(r"团队人员|人员情况表|人员配置表", title):
            staff_forms(section, src)
        origin = src.nodes[ref].text
        if re.fullmatch(r"资格证明(?:文件|材料)", title) and options and not all(n.index in copied for n in options):
            section.append(added("text", options[0].index, text="主体资格证明（按供应商实际身份选用）"))
            section.extend(added("copy", n.index, start=n.index, end=n.index) for n in options if n.index not in copied)
            section.append(added("slot", options[0].index, text="【待附】对应证件或授权文件。已随本册提交的材料可填写所在页码。"))
        is_blank = section and all(b["kind"] in ("text", "slot") for b in section)
        if is_blank and re.search("案例|业绩", title) and "合同" in title and re.search("合同复印件|合同扫描件", origin):
            count = len(set(re.findall(r"20\d\d(?=年)", origin))) if re.search("每年|各年", origin) else 1
            section.extend([added("table", ref, rows=[["合同名称", "甲方", "合同签署时间", "合同主要内容描述"]]
                                  + [["", "", "", ""] for _ in range(max(1, count))]),
                            added("slot", ref, text="【待附】按表中顺序附对应合同证明。")])
        if is_blank and re.search("承诺[书函]$", title) and re.search("提供.{0,8}承诺[书函]|承诺[书函].{0,8}盖章", origin):
            stamp = "（盖公章）" if re.search("盖.{0,3}章", origin) else ""
            section.extend([added("text", ref, text="承诺内容：________________________________"),
                            added("text", ref, text="供应商名称" + stamp + "：________________"),
                            added("text", ref, text="日期：______年____月____日")])
        blocks[pos+1:stop] = section
