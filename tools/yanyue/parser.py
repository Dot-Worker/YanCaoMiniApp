"""Whitelist-only parsing of observed encyclopedia DOM, never commerce/comments."""
from __future__ import annotations
from hashlib import sha256
import re
from .dom import Node, Tree
from .policy import ORIGIN, PolicyError, Pause, canonical_url, classify_document, utcnow

TEXT_FIELDS = {"品牌": "brand_name", "类型": "type", "包装形式": "package_form", "主颜色": "primary_color", "副颜色": "secondary_color"}
MEASUREMENTS = {
    "焦油": ("tar_mg", "mg"), "烟碱": ("nicotine_mg", "mg"), "一氧化碳": ("carbon_monoxide_mg", "mg"),
    "长度": ("length_mm", "mm"), "过滤嘴长": ("filter_length_mm", "mm"), "周长": ("circumference_mm", "mm"),
    "每盒数量": ("sticks_per_pack", "支"), "条装盒数": ("packs_per_carton", "盒"),
}
BARCODES = {"小盒条码": "pack", "条装条码": "carton"}
GROUPS = {"mainland": "大陆品牌", "foreign": "国外品牌", "HKMT": "港澳台品牌", "history": "历史品牌"}
HEALTH_NOTE = "吸烟有害健康。低焦油不代表低危害。资料仅用于识别与科普，不构成购买或使用建议。"


def first(items):
    return next(iter(items), None)


def safe_links(root, *, asset=False):
    seen = set()
    for node in root.walk("img" if asset else "a"):
        try:
            url = canonical_url(node.attrs.get("src" if asset else "href", ""), asset=asset)
        except (PolicyError, ValueError):
            continue
        if url not in seen:
            seen.add(url)
            yield url, node


def checked_tree(html):
    classification = classify_document(html)
    if classification != "content":
        raise Pause(f"document_{classification}")
    return Tree(html).root


def parse_index(html: str, *, url=ORIGIN + "/tobacco", observed_at=None):
    root = checked_tree(html)
    brands_root = first(root.walk(id="brands"))
    if brands_root is None:
        raise PolicyError("Catalog identity #brands is missing")
    brands = {}
    for group_id, group_name in GROUPS.items():
        group = first(brands_root.walk(id=group_id))
        if group is None:
            continue
        listings = [group] if "brandsList" in group.attrs.get("class", "").split() else list(group.walk(cls="brandsList"))
        for listing in listings:
            for href, a in safe_links(listing):
                if not re.fullmatch(r"https://www\.yanyue\.cn/sort/[1-9][0-9]*", href) or not a.text:
                    continue
                source_id = href.rsplit("/", 1)[1]
                brand = brands.setdefault(source_id, {"source_id": source_id, "name": a.text, "source_url": href, "source_groups": []})
                if group_name not in brand["source_groups"]:
                    brand["source_groups"].append(group_name)
    if not brands:
        raise PolicyError("Catalog contains no recognized observed brand links")
    return {"kind": "index", "source_url": canonical_url(url), "observed_at": observed_at or utcnow(), "brands": list(brands.values()), "discovered": [{"kind": "brand", "url": b["source_url"], "brand": b} for b in brands.values()]}


def evidence_field(label, value_node, observed_at, source_url, expected_unit=None):
    evidence = [url for url, _ in safe_links(value_node, asset=True)] if value_node else []
    raw = value_node.text.strip() if value_node else None
    field = {"value": None, "source_label": label, "raw_value": None, "raw_unit": None,
             "expected_unit": expected_unit, "evidence_urls": evidence, "source_url": source_url,
             "observed_at": observed_at, "verification_status": "needs_review" if evidence else "missing",
             "extraction_method": "image_not_read" if evidence else "not_available"}
    # Alt text, filenames, sibling prices and generic OCR output are not numeric evidence.
    if evidence:
        return field
    if raw:
        field["raw_value"] = raw
        field["verification_status"] = "needs_review"
        field["extraction_method"] = "source_text"
        if expected_unit:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(" + re.escape(expected_unit) + r")", raw, re.I)
            if match:
                value = float(match[1])
                field.update(value=int(value) if value.is_integer() else value, raw_unit=match[2], verification_status="source_reported")
        else:
            field.update(value=raw, verification_status="source_reported")
    return field


def parse_product(html: str, *, url: str, observed_at=None, access_method="offline_fixture"):
    if access_method not in {"offline_fixture", "normal_browser_dom"}:
        raise PolicyError("Unreviewed acquisition method")
    url = canonical_url(url)
    if not re.fullmatch(r"https://www\.yanyue\.cn/product/[1-9][0-9]*", url):
        raise PolicyError("Not a product URL")
    root = checked_tree(html)
    info = first(root.walk(id="basicinfo"))
    content = first(root.walk(id="zine_content"))
    heading = first(content.walk("h3")) if content else None
    name_node = first(heading.walk("span", cls="d-block")) if heading else None
    if info is None or name_node is None or not name_node.text:
        raise PolicyError("Missing product identity or basic facts container")
    observed_at = observed_at or utcnow()
    source_id = url.rsplit("/", 1)[1]
    pairs = {}
    for title in info.walk(cls="info_title"):
        siblings = [n for n in title.parent.children if isinstance(n, Node)]
        pos = siblings.index(title)
        if pos + 1 < len(siblings) and "info_content" in siblings[pos + 1].attrs.get("class", "").split():
            label = title.text.rstrip(":： ")
            if label in pairs:
                raise PolicyError("Duplicate fact label; requires review")
            pairs[label] = siblings[pos + 1]
    if "品牌" not in pairs or "类型" not in pairs:
        raise PolicyError("Basic facts do not match the reviewed product structure")
    fields = {name: evidence_field(label, pairs.get(label), observed_at, url) for label, name in TEXT_FIELDS.items()}
    measures = {name: evidence_field(label, pairs.get(label), observed_at, url, unit) for label, (name, unit) in MEASUREMENTS.items()}
    barcodes = []
    for label, kind in BARCODES.items():
        fact = evidence_field(label, pairs.get(label), observed_at, url)
        # Ignore UI text such as 更多; multiple image alternatives remain separate evidence.
        if fact["evidence_urls"]:
            fact["value"] = fact["raw_value"] = None
        elif fact["value"] is not None and not re.fullmatch(r"\d{8,14}", str(fact["value"])):
            fact["value"] = None
            fact["verification_status"] = "needs_review"
        fact["kind"] = kind
        barcodes.append(fact)
    images = []
    photo = first(root.walk(id="product_img"))
    if photo:
        for asset, _ in safe_links(photo, asset=True):
            if "/genpic/" in asset:
                continue
            images.append({"source_asset_url": asset, "source_page_url": url, "purpose": "package_identification",
                           "author_credit": None, "rights_status": "user_asserted_full_authorization", "rights_note": "User declared full authorization for relevant third-party images and original text; not independently verified per author.",
                           "code_license_applies": False, "allow_derivatives": None, "publication_status": "quarantined_pending_review"})
            break  # One neutral identification image, not a whole public gallery.
    aliases = [n.text for n in heading.walk("span", cls="suming") if n.text]
    record = {"schema_version": "1.0", "id": "yanyue:" + source_id, "source_id": source_id, "name": name_node.text,
              "aliases": aliases, "category": "traditional_tobacco", "production_status": "unknown", "fields": fields,
              "measurements": measures, "barcodes": barcodes, "images": images, "source_url": url, "observed_at": observed_at,
              "source_access_method": access_method, "verification_status": "source_reported", "manufacturer_verified": False,
              "source_contributor_type": "user_contributed", "health_note": HEALTH_NOTE,
              "original_text": None, "original_text_status": "not_collected_pending_scoped_source_review",
              "gallery_status": "not_collected_pending_scoped_source_review",
              "authorization": {"status": "user_asserted_full_authorization", "scope": "relevant_third_party_images_and_original_encyclopedia_text", "independently_verified": False, "code_license_applies": False}}
    return {"kind": "product", "source_url": url, "observed_at": observed_at, "product": record, "discovered": []}


def parse_page(html, *, url, observed_at=None, access_method="offline_fixture"):
    url = canonical_url(url)
    if url.endswith("/tobacco"):
        result = parse_index(html, url=url, observed_at=observed_at)
    elif "/product/" in url:
        result = parse_product(html, url=url, observed_at=observed_at, access_method=access_method)
    else:
        result = parse_brand(html, url=url, observed_at=observed_at)
    result["source_access_method"] = access_method
    # Hash the source document, but never persist raw HTML/session links/script text.
    result["source_content_sha256"] = sha256(html.encode("utf-8")).hexdigest()
    return result


def parse_brand(html, *, url, observed_at=None):
    url = canonical_url(url)
    match = re.fullmatch(r"https://www\.yanyue\.cn/sort/(?:([1-9][0-9]*)|sort/sortid/([1-9][0-9]*)/p/([1-9][0-9]*))", url)
    if not match:
        raise PolicyError("Not an observed brand page route")
    brand_id = match[1] or match[2]
    root = checked_tree(html)
    content = first(root.walk(id="zine_content"))
    wrapper = first(root.walk(id="prowrap"))
    heading = first(content.walk("h3")) if content else None
    if wrapper is None or heading is None or not heading.text:
        raise PolicyError("Brand identity or listing container is absent")
    observed_at = observed_at or utcnow()
    products = {}
    for row in wrapper.children:
        if not isinstance(row, Node) or "table" not in row.attrs.get("class", "").split():
            continue
        name_cell = first(row.walk(cls="name2"))
        if name_cell:
            for target, a in safe_links(name_cell):
                if "/product/" in target and a.text:
                    products[target] = {"kind": "product", "url": target, "name": a.text, "brand_source_id": brand_id}
    nav = first(root.walk("nav", cls="pagenav_mainarea"))
    discovered = list(products.values())
    reported_count = reported_pages = current_page = None
    if nav:
        count = re.search(r"共\s*(\d+)\s*条记录", nav.text)
        reported_count = int(count[1]) if count else None
        page_node = first(nav.walk(cls="pagenav_currentpage"))
        total_node = first(nav.walk(cls="pagenav_totalnum"))
        current_page = int(page_node.text) if page_node and page_node.text.isdigit() else None
        reported_pages = int(total_node.text) if total_node and total_node.text.isdigit() else None
        candidates = [n.attrs.get("href", "") for n in nav.walk("a")]
        for select in nav.walk("select", cls="pagenav_pagechg"):
            candidates += [n.attrs.get("value", "") for n in select.walk("option")]
        seen = {url}
        for href in candidates:
            try:
                target = canonical_url(href)
            except (PolicyError, ValueError):
                continue
            if target not in seen and (target == f"{ORIGIN}/sort/{brand_id}" or re.fullmatch(re.escape(ORIGIN) + rf"/sort/sort/sortid/{brand_id}/p/[1-9][0-9]*", target)):
                seen.add(target)
                discovered.append({"kind": "brand", "url": target, "brand_source_id": brand_id})
    expected_page = int(match[3] or "1")
    if current_page is not None and current_page != expected_page:
        raise PolicyError("Observed page number does not match requested page")
    if not products and reported_count != 0:
        raise PolicyError("No recognized listing rows; not silently treated as complete")
    if reported_pages is not None and reported_pages > 1 and not any(d["kind"] == "brand" for d in discovered):
        raise PolicyError("Pagination advertised but no safe links could be observed")
    return {"kind": "brand", "source_url": url, "observed_at": observed_at,
            "brand_source_id": brand_id, "brand_name": heading.text,
            "reported_product_count": reported_count, "reported_page_count": reported_pages,
            "observed_page": current_page, "observed_page_items": len(products), "discovered": discovered}
