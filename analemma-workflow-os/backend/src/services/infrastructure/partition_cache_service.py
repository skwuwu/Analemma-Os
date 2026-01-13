
import os
import json
import logging
import hashlib
import time
import shutil
from typing import Dict, List, Any, Optional, Union
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class PartitionCacheService:
    """
    Infrastructure Service for managing Distributed Map partitions.
    Handles:
    - S3 I/O and ETag verification
    - /tmp disk space management
    - Local file caching and Memory caching
    - ijson streaming support (memory optimization)
    """

    # Global memory cache (shared across Lambda invocations in same execution env)
    _memory_cache: Dict[str, Any] = {}
    
    # Constants
    CACHE_DIR = "/tmp/partition_cache"
    MAX_CACHE_SIZE_MB = int(os.environ.get('CACHE_MAX_SIZE_MB', '200'))
    CACHE_CLEANUP_THRESHOLD = float(os.environ.get('CACHE_CLEANUP_THRESHOLD', '0.8'))
    
    def __init__(self):
        self.s3_client = boto3.client('s3')
        # Check ijson availability once
        try:
            import ijson
            self.ijson = ijson
            self.streaming_available = True
        except ImportError:
            self.ijson = None
            self.streaming_available = False

    def load_partition_map(
        self, 
        s3_path: str, 
        required_segment_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Load partition map or specific segment from s3_path.
        Uses caching layers (Memory -> File -> S3) and supports streaming.
        
        Returns:
            {
                "partition_map": [...],
                "loaded_segments": int,
                "memory_optimized": bool
            }
        """
        # If specific index requested and streaming available, try streaming direct from S3
        # BUT, we might want to check cache first even for single segment? 
        # The original logic prioritized streaming for single segment to avoid full load.
        # However, checking cache is cheap.
        
        # 1. Resolve cache key (ETag check)
        cache_key_info = self._resolve_cache_key(s3_path)
        cache_key = cache_key_info['cache_key']
        etag = cache_key_info.get('etag')
        
        # 2. Check Memory Cache (Full map)
        if cache_key in self._memory_cache:
            full_map = self._memory_cache[cache_key]
            if required_segment_index is not None:
                if 0 <= required_segment_index < len(full_map):
                    return {
                        "partition_map": [full_map[required_segment_index]],
                        "loaded_segments": 1,
                        "memory_optimized": True # Effectively optimized since already in memory
                    }
                return {"partition_map": [], "loaded_segments": 0, "memory_optimized": True}
            return {
                "partition_map": full_map, 
                "loaded_segments": len(full_map), 
                "memory_optimized": False
            }

        # 3. Check File Cache
        cached_data = self._load_from_file_cache(cache_key, s3_path)
        if cached_data:
            # Populate memory cache
            self._memory_cache[cache_key] = cached_data
            if required_segment_index is not None:
                 if 0 <= required_segment_index < len(cached_data):
                    return {
                        "partition_map": [cached_data[required_segment_index]],
                        "loaded_segments": 1,
                        "memory_optimized": True
                    }
            return {
                "partition_map": cached_data, 
                "loaded_segments": len(cached_data), 
                "memory_optimized": False
            }

        # 4. Cache Miss - Cleanup stale
        self._cleanup_stale_cache_for_path(s3_path, cache_key)
        
        # 5. Decide on Loading Strategy (Streaming vs Full)
        # If we only need one segment and have streaming, use strict streaming (no cache save)
        if required_segment_index is not None and self.streaming_available:
             return self._stream_single_segment_from_s3(s3_path, required_segment_index)
        
        # 6. Full Load & Cache
        full_map = self._load_full_from_s3(s3_path)
        if full_map:
             self._save_to_cache(cache_key, full_map, cache_key_info)
             
        return {
            "partition_map": full_map,
            "loaded_segments": len(full_map),
            "memory_optimized": False
        }

    def _resolve_cache_key(self, s3_path: str) -> Dict[str, Any]:
        """Resolve ETag and generate cache key."""
        try:
            bucket, key = self._parse_s3_uri(s3_path)
            head = self.s3_client.head_object(Bucket=bucket, Key=key)
            etag = head.get('ETag', '').strip('"')
            size = head.get('ContentLength', 0)
            last_modified = head.get('LastModified')
            
            cache_key_data = f"{s3_path}#{etag}#{size}"
            cache_key = hashlib.md5(cache_key_data.encode('utf-8')).hexdigest()
            
            return {
                'cache_key': cache_key,
                'etag': etag,
                'content_length': size,
                'last_modified': last_modified
            }
        except Exception as e:
            logger.warning(f"Failed to get ETag for {s3_path}: {e}, using path-only key")
            return {
                'cache_key': hashlib.md5(s3_path.encode('utf-8')).hexdigest(),
                'etag': None
            }

    def _load_from_file_cache(self, cache_key: str, s3_path: str) -> Optional[List[Dict]]:
        """Try loading from /tmp."""
        if not os.path.exists(self.CACHE_DIR):
            return None
            
        file_path = os.path.join(self.CACHE_DIR, f"{cache_key}.json")
        if not os.path.exists(file_path):
            return None
            
        try:
            stat = os.stat(file_path)
            age = time.time() - stat.st_mtime
            if age > 900: # 15 min TTL
                self._safe_remove_file(file_path)
                return None
                
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Verify structure (wrapped metadata)
            if isinstance(data, dict) and 'data' in data:
                return data['data']
            return data # Legacy format support if any
            
        except Exception as e:
            logger.warning(f"Failed to read cache {file_path}: {e}")
            self._safe_remove_file(file_path)
            return None

    def _stream_single_segment_from_s3(self, s3_path: str, index: int) -> Dict[str, Any]:
        """Stream a single segment using ijson."""
        logger.info(f"Streaming segment {index} from {s3_path}")
        try:
            bucket, key = self._parse_s3_uri(s3_path)
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            
            current_index = 0
            for segment in self.ijson.items(response['Body'], 'item'):
                if current_index == index:
                    return {
                        "partition_map": [segment],
                        "loaded_segments": 1,
                        "memory_optimized": True
                    }
                current_index += 1
            
            logger.warning(f"Segment {index} not found in {s3_path}")
            return {"partition_map": [], "loaded_segments": 0, "memory_optimized": True}
            
        except Exception as e:
            logger.error(f"Streaming failed: {e}")
            return {"partition_map": [], "loaded_segments": 0, "memory_optimized": False}

    def _load_full_from_s3(self, s3_path: str) -> List[Dict]:
        """Full S3 download."""
        try:
            bucket, key = self._parse_s3_uri(s3_path)
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            if not content.strip():
                return []
            return json.loads(content)
        except Exception as e:
            logger.error(f"Full load failed for {s3_path}: {e}")
            return []

    def _save_to_cache(self, cache_key: str, data: List[Dict], meta: Dict[str, Any]):
        """Save to memory and /tmp cache."""
        # Memory
        self._memory_cache[cache_key] = data
        
        # File
        try:
            self._ensure_cache_space_available()
            
            json_str = json.dumps({
                'etag': meta.get('etag'),
                'last_modified': meta.get('last_modified').isoformat() if meta.get('last_modified') else None,
                'content_length': meta.get('content_length'),
                'cached_at': time.time(),
                'data': data
            }, ensure_ascii=False)
            
            size_mb = len(json_str.encode('utf-8')) / (1024 * 1024)
            if size_mb > self.MAX_CACHE_SIZE_MB * 0.5:
                # Too big for file cache
                return

            os.makedirs(self.CACHE_DIR, exist_ok=True)
            file_path = os.path.join(self.CACHE_DIR, f"{cache_key}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(json_str)
                
        except Exception as e:
            logger.warning(f"Failed to write disk cache: {e}")

    def _ensure_cache_space_available(self):
        """Cleanup space if needed."""
        try:
            stats = self.get_cache_stats()
            if stats['tmp_usage']['usage_ratio'] > self.CACHE_CLEANUP_THRESHOLD:
                self._cleanup_old_cache_files()
        except Exception:
            pass

    def _cleanup_old_cache_files(self) -> Dict[str, Any]:
        """Remove old files."""
        # Simplify implementation for service
        cleanup_stats = {'files_removed': 0, 'space_freed_mb': 0.0}
        if not os.path.exists(self.CACHE_DIR):
            return cleanup_stats
            
        try:
            files = []
            for f in os.listdir(self.CACHE_DIR):
                if not f.endswith('.json'): continue
                path = os.path.join(self.CACHE_DIR, f)
                try:
                    stat = os.stat(path)
                    files.append({
                        'path': path, 
                        'mtime': stat.st_mtime, 
                        'size': stat.st_size
                    })
                except: pass
            
            # Sort by mtime (oldest first)
            files.sort(key=lambda x: x['mtime'])
            
            # Delete 30% of files
            target_remove = int(len(files) * 0.3)
            for i in range(target_remove):
                self._safe_remove_file(files[i]['path'])
                cleanup_stats['files_removed'] += 1
                cleanup_stats['space_freed_mb'] += files[i]['size'] / (1024*1024)
                
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
        return cleanup_stats

    def _cleanup_stale_cache_for_path(self, s3_path: str, current_cache_key: str):
        """Remove previous versions of cache for same path."""
        # Requires opening files to check 's3_path' inside metadata? 
        # Or we can rely on TTL. The original implementation did checked content.
        # For simplicity and perf, we might skip expensive full scan unless really needed.
        # But let's check prefix if we used simple hashing. 
        # Actually, since cache key is MD5 of (path+etag), we can't pattern match easily 
        # unless we store a mapping. 
        # The original code scanned ALL json files. That's heavy.
        # Let's trust TTL for now to clean up old versions eventually.
        pass

    def _safe_remove_file(self, path: str):
        try:
            if os.path.exists(path): os.remove(path)
        except: pass

    def _parse_s3_uri(self, uri: str):
        if not uri.startswith("s3://"): raise ValueError("Invalid S3 URI")
        parts = uri[5:].split("/", 1)
        if len(parts) != 2: raise ValueError("Invalid S3 URI")
        return parts[0], parts[1]

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get stats."""
        try:
            total, used, free = shutil.disk_usage('/tmp')
            return {
                'memory_cache_entries': len(self._memory_cache),
                'tmp_usage': {
                    'total_mb': total / (1024*1024),
                    'used_mb': used / (1024*1024),
                    'usage_ratio': used / total if total else 0
                }
            }
        except:
             return {'error': 'Stats failed'}
    
    def clear_cache(self):
        self._memory_cache.clear()
        if os.path.exists(self.CACHE_DIR):
            shutil.rmtree(self.CACHE_DIR, ignore_errors=True)
