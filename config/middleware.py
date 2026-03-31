"""
Custom middleware for bekrin-back.
"""
from django.utils.deprecation import MiddlewareMixin
import logging

logger = logging.getLogger(__name__)


class FrameOptionsExemptMiddleware(MiddlewareMixin):
    """
    Remove X-Frame-Options for responses that must be embeddable in iframes
    (e.g. PDF preview on teacher exam detail, media files).
    Must run after django.middleware.clickjacking.XFrameOptionsMiddleware.
    """
    # Path prefixes that may be loaded in iframes (PDF preview, media)
    FRAME_EXEMPT_PREFIXES = ('/media/', '/protected-media/', '/api/student/runs/')

    def process_response(self, request, response):
        path = request.path
        content_type = response.get('Content-Type', '')
        is_pdf = 'application/pdf' in content_type.lower() or path.lower().endswith('.pdf') or '/pdf' in path.lower()
        # Never access response.content for PDF: streaming responses would be consumed and PDF would appear empty in iframe.
        
        # Check if this is a path that should be embeddable
        should_exempt = any(path.startswith(prefix) for prefix in self.FRAME_EXEMPT_PREFIXES)
        
        # Also exempt PDF files by content-type or file extension
        if not should_exempt:
            if 'application/pdf' in content_type.lower():
                should_exempt = True
            elif path.lower().endswith('.pdf'):
                should_exempt = True
        
        # Check query parameters for PDF indicators (some views might use query params)
        if not should_exempt and 'pdf' in path.lower():
            should_exempt = True
        
        if should_exempt:
            # Remove X-Frame-Options header if present (case-insensitive check)
            headers_to_remove = []
            for header_name in response:
                if header_name.lower() == 'x-frame-options':
                    headers_to_remove.append(header_name)
            
            for header_name in headers_to_remove:
                del response[header_name]
                logger.debug(f"Removed X-Frame-Options for path: {path}")
            
            # Don't set X-Frame-Options at all - this allows cross-origin embedding
            # which is needed when frontend (localhost:3000) embeds backend (localhost:8000) content
            response['X-Frame-Allowed'] = 'true'
        
        return response
