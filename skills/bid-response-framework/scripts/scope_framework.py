"""Select a document scope from a source-bound response plan. No reference DOCX is read."""
from copy import deepcopy
import re

SCOPES = ("whole", "identity-and-authorization")
SCOPE_TITLE = "主体资格与授权分册"
ROLES = {
    "letter": r"投标函",
    "license": r"(?:企业(?:法人)?|公司)?营业执照(?:正本|副本|复印件|副本复印件)?",
    "identity": r"(?:法人|法人代表|法定代表人|单位负责人)(?:的)?身份证(?:明(?:书)?|复印件|正反面(?:复印件)?)",
    "authority": r"(?:(?:法定代表人|法人代表|单位负责人|法人)(?:的)?)?(?:授权委托书|授权书)",
    "profile": r"(?:投标人|投标单位|供应商|应答人|企业)(?:的)?(?:基本)?(?:情况|概况|信息)(?:一览)?表",
    "channel": r"(?:(?:竞标|投标|所投)?(?:广告)?(?:媒体|渠道|平台)(?:的)?)代理(?:资格|资质)(?:证明(?:文件)?|证书)?",
}
OFF_TOPIC = re.compile(r"技术(?:部分|方案|标)|商务(?:标应答|讲标)|费用|分项报价|报价明细|财务|审计|业绩|项目案例|拟派人员|实施团队|讲标")


def role(title):
    return next((label for label, pattern in ROLES.items() if re.fullmatch(pattern, title)), None)


def channel_names(plan):
    plain = [line.strip() for block in plan["blocks"] if block["kind"] == "text"
             for line in block.get("text", "").splitlines()]
    names = []
    for line in plain:
        match = re.match(r"^([\u3400-\u9fffA-Za-z0-9·]{1,16})媒体[（(]信息流[）)][：:]", line)
        if match and match[1] not in names:
            names.append(match[1])
    project = plan.get("project") or ""
    if names and all(project.count(name) == 1 for name in names):
        names.sort(key=project.index)
    return names


def apply_scope(plan, scope="whole"):
    if scope == "whole":
        return plan
    if scope not in SCOPES:
        raise ValueError(f"未知文档范围 {scope}。支持 {'、'.join(SCOPES)}。")
    blocks = plan["blocks"]
    starts = [i for i, b in enumerate(blocks) if b["kind"] == "heading"]
    if not starts:
        raise ValueError("主体资格与授权分册需要有来源的正文标题。")
    front = blocks[:starts[0]]
    toc = next((i for i, b in enumerate(front) if b["kind"] == "toc"), None)
    if toc is None:
        raise ValueError("无法区分封面、目录和正文，请先复核完整编排计划。")
    cover_end = next((i for i, b in enumerate(front[:toc]) if b["kind"] == "break"), toc)
    # A scoring index between the cover and the TOC belongs to the whole bid.
    output = deepcopy(front[:cover_end])
    output.extend([{"kind": "break"}, {"kind": "text", "text": SCOPE_TITLE},
                   deepcopy(front[toc]), {"kind": "break"}])
    included = set()
    role_set = set()
    media = channel_names(plan)
    ancestors = []
    previous = None
    for a, b in zip(starts, starts[1:] + [len(blocks)]):
        heading = blocks[a]
        title = re.sub(r"\s+", "", heading["text"]).strip("：:。")
        level = heading.get("level", 1)
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        tag = role(title)
        # An otherwise generic undertaking is included only when a media/channel
        # authorization already in scope requires that undertaking.
        related = previous and previous[0] == "channel" and (
            previous[1] == heading.get("source_index") or "承诺函" in previous[2] or "承诺书" in previous[2])
        if re.fullmatch(r"(?:代理资质|媒体代理|渠道代理)?授权承诺[书函]", title) and (
                related or any(item[2] == "channel" for item in ancestors)):
            tag = "channel-promise"
        if any(OFF_TOPIC.search(item[1]) for item in ancestors):
            tag = None
        if tag:
            section = deepcopy(blocks[a:b])
            section[0]["level"] = 1 + sum(item[2] is not None for item in ancestors)
            if title == "营业执照副本" and any("副本" in child.get("text", "") for child in section[1:]):
                section[0]["text"] = "营业执照"
            elif tag == "channel" and len(media) >= 2:
                section[0]["text"] += "（" + "/".join(media) + "）"
            output.extend(section)
            role_set.add(tag)
            for block in section:
                if "source_index" in block:
                    included.add(block["source_index"])
                if block["kind"] == "copy":
                    included.update(range(block["start"], block.get("end", block["start"]) + 1))
        comment = " ".join(x.get("text", "") for x in blocks[a+1:b])
        previous = (tag, heading.get("source_index"), comment)
        ancestors.append((level, title, tag))
    if "letter" not in role_set or "authority" not in role_set or not role_set.intersection({"identity", "license"}):
        raise ValueError("未识别到独立投标函、代表授权文书及对应主体身份证明。当前项目不支持自动提取主体资格与授权分册，请核对完整框架。")
    notes = [deepcopy(note) for note in plan.get("review", []) if note.get("source_index") in included]
    notes.append({"text": SCOPE_TITLE + "仅包含主体与代理授权材料。按采购文件另行准备其余投标材料。"})
    return {**plan, "scope": scope, "review": notes, "blocks": output, "parts": []}
