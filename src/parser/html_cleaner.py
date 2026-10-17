"""
HTML content cleaner for removing boilerplate and extracting main content.
"""

from bs4 import BeautifulSoup
import re
from typing import Optional


class HTMLCleaner:
    """
    Extract main content from HTML, removing navigation, footer, scripts,
    and other boilerplate elements.
    """

    @staticmethod
    def clean(html_content: str) -> str:
        """
        Clean HTML content and extract meaningful text.

        Args:
            html_content: Raw HTML string.

        Returns:
            Cleaned text content.
        """
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, 'html.parser')

        # Remove script and style elements
        for element in soup.find_all(['script', 'style']):
            element.decompose()

        # Remove navigation elements
        for element in soup.find_all(['nav', 'footer', 'header', 'aside']):
            element.decompose()

        # Remove elements with navigation-related classes
        nav_patterns = re.compile(r'(nav|menu|footer|header|sidebar|banner|cookie|popup|modal)', re.I)
        for element in soup.find_all(class_=nav_patterns):
            element.decompose()
        for element in soup.find_all(id=nav_patterns):
            element.decompose()

        # Prefer main content containers
        main_content = (
            soup.find('main') or
            soup.find('article') or
            soup.find(class_=re.compile(r'content|body|main', re.I)) or
            soup.find('body')
        )

        if main_content:
            text = main_content.get_text(separator='\n', strip=True)
        else:
            text = soup.get_text(separator='\n', strip=True)

        # Clean up whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        return '\n'.join(lines)

    @staticmethod
    def clean_file(input_path: str, output_path: Optional[str] = None) -> str:
        """
        Clean HTML from a file and optionally save to output path.

        Args:
            input_path: Path to input HTML file.
            output_path: Optional path to save cleaned content.

        Returns:
            Cleaned text content.
        """
        with open(input_path, 'r', encoding='utf-8') as f:
            html = f.read()

        cleaned = HTMLCleaner.clean(html)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(cleaned)

        return cleaned