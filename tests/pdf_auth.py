"""
Signed URL authentication for PDF access in iframes.

Since iframes cannot send Authorization headers, we use signed URLs
that contain a time-limited token allowing access to specific PDFs.
"""
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

# Token expires after 1 hour (same as JWT access token default)
PDF_TOKEN_MAX_AGE = timedelta(hours=1)


def generate_pdf_access_token(user_id, run_id):
    """
    Generate a signed token for PDF access.
    
    Args:
        user_id: ID of the user requesting access
        run_id: ID of the exam run
        
    Returns:
        Signed token string
    """
    signer = TimestampSigner(salt='pdf-access')
    # Sign user_id:run_id to ensure user can only access their own runs
    value = f"{user_id}:{run_id}"
    return signer.sign(value)


def validate_pdf_access_token(token, run_id):
    """
    Validate a signed PDF access token.
    
    Args:
        token: Signed token from query parameter
        run_id: Expected run_id (must match token)
        
    Returns:
        tuple: (is_valid, user_id) - user_id is None if invalid
        
    Raises:
        BadSignature: Token is malformed or tampered
        SignatureExpired: Token has expired
    """
    signer = TimestampSigner(salt='pdf-access')
    try:
        # Unsign and verify timestamp
        unsigned_value = signer.unsign(token, max_age=PDF_TOKEN_MAX_AGE.total_seconds())
        user_id_str, token_run_id_str = unsigned_value.split(':')
        user_id = int(user_id_str)
        token_run_id = int(token_run_id_str)
        
        # Verify run_id matches
        if token_run_id != run_id:
            logger.warning(f"PDF token run_id mismatch: token={token_run_id}, expected={run_id}")
            return False, None
            
        return True, user_id
    except (BadSignature, SignatureExpired, ValueError) as e:
        logger.warning(f"PDF token validation failed: {type(e).__name__}: {e}")
        return False, None
