import re
from typing import Any, Dict, List


class SpecsParser:
    def _clean_markdown(self, text: str) -> str:
        if not text:
            return ""
        clean = text
        clean = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", clean)
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", clean)
        clean = re.sub(r"\*(.+?)\*", r"\1", clean)
        clean = re.sub(r"__(.+?)__", r"\1", clean)
        clean = re.sub(r"_(.+?)_", r"\1", clean)
        clean = re.sub(r"~~(.+?)~~", r"\1", clean)
        clean = re.sub(r"`([^`]+)`", r"\1", clean)
        clean = re.sub(r"^#{1,6}\s*", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"^\s*[-*+]\s+", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"^\s*\d+[.)]\s+", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
        clean = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", clean)
        clean = re.sub(r"^>\s*", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        clean = re.sub(r"[ \t]+", " ", clean)
        return clean.strip()

    def _clean_feature_name(self, name: str, feature_id: str) -> str:
        if not name:
            return ""
        clean = self._clean_markdown(name)
        clean = re.sub(
            r"^[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\U0001FA00-\U0001FAFF]+\s*",
            "",
            clean,
        )
        patterns = [
            r"^(FEAT-\d+|Feature\s*\d+|[A-Z]+-\d+)[:\s\-]+",
            r"^(FEAT-\d+|Feature\s*\d+|[A-Z]+-\d+)\s*$",
        ]
        for pattern in patterns:
            clean = re.sub(pattern, "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"^[:\-\s]+", "", clean).strip()
        return clean if clean else feature_id

    def parse(self, specs_path: str) -> List[Dict[str, Any]]:
        with open(specs_path, "r", encoding="utf-8") as file:
            content = file.read()

        features = self._parse_structured_features(content)

        if not features or all(len(f.get("acceptance_criteria", [])) == 0 for f in features):
            features = self._parse_flexible_format(content)

        if not features:
            features = self._fallback_parse(content)

        return features

    def _parse_structured_features(self, content: str) -> List[Dict[str, Any]]:
        features = []

        feature_patterns = [
            r"###\s*(FEAT-\d+|Feature\s*\d+|[A-Z]+-\d+)[:\s\-]*(.+?)(?=###|\Z)",
            r"##\s*(FEAT-\d+|Feature\s*\d+)[:\s\-]*(.+?)(?=##\s*[A-Z]|\Z)",
        ]

        for pattern in feature_patterns:
            matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
            for match in matches:
                feature_id = match[0].strip()
                feature_content = match[1].strip() if len(match) > 1 else ""

                lines = feature_content.split("\n")
                name = lines[0].strip() if lines else feature_id
                description = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

                features.append(
                    {
                        "id": feature_id,
                        "name": self._clean_feature_name(name, feature_id),
                        "description": self._clean_markdown(description[:500]),
                        "priority": self._extract_priority(feature_content),
                        "status": self._extract_status(feature_content),
                        "related_components": self._extract_components(feature_content),
                        "acceptance_criteria": self._extract_acceptance_criteria(feature_content),
                    }
                )

            if features:
                break

        return features

    def _parse_flexible_format(self, content: str) -> List[Dict[str, Any]]:
        features = []
        header_patterns = [r"^##\s+(.+?)$", r"^###\s+(.+?)$"]

        sections = []
        current_header = None
        current_content = []

        for line in content.split("\n"):
            is_header = False
            for pattern in header_patterns:
                match = re.match(pattern, line.strip())
                if match:
                    if current_header:
                        sections.append((current_header, "\n".join(current_content)))
                    current_header = match.group(1).strip()
                    current_content = []
                    is_header = True
                    break

            if not is_header and current_header:
                current_content.append(line)

        if current_header:
            sections.append((current_header, "\n".join(current_content)))

        skip_headers = [
            "overview",
            "introduction",
            "getting started",
            "installation",
            "requirements",
            "table of contents",
            "toc",
            "index",
            "summary",
            "changelog",
            "license",
            "contributing",
            "acknowledgments",
        ]

        for index, (header, section_content) in enumerate(sections):
            header_lower = header.lower()
            if any(skip in header_lower for skip in skip_headers):
                continue

            if len(header) < 3:
                continue

            acceptance_criteria = self._extract_acceptance_criteria(section_content)
            if not acceptance_criteria:
                acceptance_criteria = self._extract_bullet_points_as_criteria(section_content)

            if acceptance_criteria or len(section_content.strip()) > 50:
                lines = section_content.strip().split("\n")
                description = " ".join(lines[:3]).strip()[:300] if lines else ""

                features.append(
                    {
                        "id": f"FEAT-{index + 1}",
                        "name": self._clean_feature_name(header, f"FEAT-{index + 1}"),
                        "description": self._clean_markdown(description),
                        "priority": self._extract_priority(section_content),
                        "status": self._extract_status(section_content),
                        "related_components": self._extract_components(section_content),
                        "acceptance_criteria": acceptance_criteria,
                    }
                )

        return features

    def _extract_acceptance_criteria(self, content: str) -> List[Dict[str, Any]]:
        criteria = []
        ac_patterns = [
            r"(?:Acceptance\s*Criteria|AC|Criteria|Requirements|Must|Should)[:\s]*\n((?:[-*]\s*.+\n?)+)",
            r"(?:Given|When|Then)\s+.+",
        ]

        for pattern in ac_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]

                bullets = re.findall(r"[-*]\s*(.+?)(?=\n[-*]|\n\n|\Z)", match, re.DOTALL)
                for bullet in bullets:
                    bullet = bullet.strip()
                    if len(bullet) > 10:
                        criteria.append(
                            {
                                "id": f"AC-{len(criteria) + 1}",
                                "description": self._clean_markdown(bullet[:500]),
                                "referenced_files": re.findall(r"`([^`]+\.[a-zA-Z]+)`", bullet),
                            }
                        )

        gherkin = re.findall(
            r"(Given\s+.+?(?:When\s+.+?)?(?:Then\s+.+?)?)(?=Given|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        for scenario in gherkin:
            scenario = scenario.strip()
            if len(scenario) > 20:
                criteria.append(
                    {
                        "id": f"AC-{len(criteria) + 1}",
                        "description": self._clean_markdown(scenario[:500]),
                        "referenced_files": [],
                    }
                )

        return criteria

    def _extract_bullet_points_as_criteria(self, content: str) -> List[Dict[str, Any]]:
        criteria = []

        bullets = re.findall(r"^[-*]\s+(.+?)$", content, re.MULTILINE)
        bullets.extend(re.findall(r"^\d+[.)]\s+(.+?)$", content, re.MULTILINE))

        metadata_patterns = [
            r"^\*\*?(description|priority|status|related\s*components?|components?)\*?\*?[:\s]",
            r"^(high|medium|low|critical|done|completed|in\s*progress|todo|planned)\s*$",
            r"^(✅|❌|🚧)\s*(priority|status|description|components?)",
        ]

        for index, bullet in enumerate(bullets):
            bullet = bullet.strip()
            if any(re.match(pattern, bullet, re.IGNORECASE) for pattern in metadata_patterns):
                continue

            if len(bullet) > 15 and len(bullet) < 500:
                criteria.append(
                    {
                        "id": f"AC-{index + 1}",
                        "description": self._clean_markdown(bullet),
                        "referenced_files": re.findall(r"`([^`]+\.[a-zA-Z]+)`", bullet),
                    }
                )

        return criteria[:20]

    def _extract_priority(self, content: str) -> str:
        match = re.search(r"\*\*Priority\*\*[:\s]*(\w+)", content, re.IGNORECASE)
        if match:
            return match.group(1)

        if re.search(r"\b(critical|urgent|p0|p1)\b", content, re.IGNORECASE):
            return "High"
        if re.search(r"\b(important|p2)\b", content, re.IGNORECASE):
            return "Medium"
        if re.search(r"\b(nice.to.have|low|p3|p4)\b", content, re.IGNORECASE):
            return "Low"

        return "Medium"

    def _extract_status(self, content: str) -> str:
        match = re.search(r"\*\*Status\*\*[:\s]*(\w+)", content, re.IGNORECASE)
        if match:
            return match.group(1)

        if re.search(r"\b(done|completed|implemented|✅)\b", content, re.IGNORECASE):
            return "Done"
        if re.search(r"\b(in.progress|wip|🚧)\b", content, re.IGNORECASE):
            return "In Progress"
        if re.search(r"\b(todo|planned|❌)\b", content, re.IGNORECASE):
            return "Todo"

        return "Unknown"

    def _extract_components(self, content: str) -> List[str]:
        match = re.search(
            r"\*\*(?:Related\s*)?Components?\*\*[:\s]*(.+?)(?=\*\*|\n\n|$)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            components_text = match.group(1)
            components = re.findall(r"`([^`]+)`", components_text)
            if not components:
                components = [component.strip() for component in components_text.split(",") if component.strip()]
            return components[:10]

        files = re.findall(r"`([^`]+\.[a-zA-Z]+)`", content)
        return list(set(files))[:10]

    def _fallback_parse(self, content: str) -> List[Dict[str, Any]]:
        features = []
        current_feature = None
        current_content = []

        for line in content.split("\n"):
            if line.startswith("#"):
                if current_feature:
                    content_str = "\n".join(current_content)
                    current_feature["acceptance_criteria"] = self._extract_bullet_points_as_criteria(content_str)
                    current_feature["description"] = self._clean_markdown(content_str[:300])
                    features.append(current_feature)

                header_text = line.lstrip("#").strip()
                if len(header_text) > 3:
                    current_feature = {
                        "id": f"FEAT-{len(features) + 1}",
                        "name": self._clean_feature_name(header_text, f"FEAT-{len(features) + 1}"),
                        "description": "",
                        "priority": "Medium",
                        "status": "Unknown",
                        "related_components": [],
                        "acceptance_criteria": [],
                    }
                    current_content = []
            elif current_feature:
                current_content.append(line)

        if current_feature:
            content_str = "\n".join(current_content)
            current_feature["acceptance_criteria"] = self._extract_bullet_points_as_criteria(content_str)
            current_feature["description"] = self._clean_markdown(content_str[:300])
            features.append(current_feature)

        return features[:20]
