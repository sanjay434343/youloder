from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict, Any
import yt_dlp
import os
import tempfile
import asyncio
import uvicorn
from urllib.parse import urlparse, parse_qs
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Downloader API",
    description="Download YouTube videos from various link formats",
    version="1.0.0"
)

# CORS middleware with all origins allowed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create downloads directory
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Pydantic models
class VideoDownloadRequest(BaseModel):
    url: str
    format: Optional[str] = "best"  # best, worst, mp4, mp3, etc. or format_id
    quality: Optional[str] = "best"  # best, worst, or specific like 1080p, 720p

class PlaylistDownloadRequest(BaseModel):
    url: str
    format: Optional[str] = "best"
    quality: Optional[str] = "best"  # best, worst, or specific quality
    start_index: Optional[int] = 1
    end_index: Optional[int] = None

class VideoInfo(BaseModel):
    id: str
    title: str
    duration: int
    view_count: int
    upload_date: str
    uploader: str
    description: str
    thumbnail: str
    formats: List[Dict[str, Any]]

class DownloadResponse(BaseModel):
    success: bool
    message: str
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    download_url: Optional[str] = None

class YouTubeDownloader:
    def __init__(self):
        self.ydl_opts_base = {
            'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s'),
            'restrictfilenames': True,
            'noplaylist': True,
        }
    
    def normalize_url(self, url: str) -> str:
        """Normalize various YouTube URL formats to standard format"""
        # Handle youtu.be short links
        if 'youtu.be' in url:
            video_id = url.split('/')[-1].split('?')[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        
        # Handle m.youtube.com mobile links
        if 'm.youtube.com' in url:
            url = url.replace('m.youtube.com', 'www.youtube.com')
        
        # Handle youtube.com/embed/ links
        if '/embed/' in url:
            video_id = url.split('/embed/')[-1].split('?')[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        
        # Handle youtube.com/v/ links
        if '/v/' in url:
            video_id = url.split('/v/')[-1].split('?')[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        
        return url
    
    def is_playlist_url(self, url: str) -> bool:
        """Check if URL is a playlist"""
        return 'playlist?list=' in url or '&list=' in url
    
    def get_video_info(self, url: str) -> Dict[str, Any]:
        """Get video information without downloading"""
        normalized_url = self.normalize_url(url)
        
        ydl_opts = {
            **self.ydl_opts_base,
            'skip_download': True,
            'listformats': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(normalized_url, download=False)
                return info
            except Exception as e:
                error_msg = str(e)
                if (
                    "Sign in to confirm you’re not a bot" in error_msg
                    or "cookies" in error_msg.lower()
                    or "authentication" in error_msg.lower()
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "YouTube is requiring authentication to access this video. "
                            "This may happen if YouTube suspects automated traffic or the video is age-restricted. "
                            "Try again later, or use yt-dlp with cookies as described at: "
                            "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
                        )
                    )
                raise HTTPException(status_code=400, detail=f"Failed to extract video info: {error_msg}")
    
    def download_video(self, url: str, format_selector: str = "best", quality: str = "720p") -> Dict[str, Any]:
        """Download a single video"""
        normalized_url = self.normalize_url(url)

        # If format_selector looks like a format_id (all digits or contains dash), use it directly
        if format_selector and (format_selector.isdigit() or '-' in format_selector):
            format_string = format_selector
            postprocessors = []
        elif format_selector == "mp3":
            format_string = "bestaudio/best"
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif format_selector == "mp4":
            if quality in ["best", "worst"]:
                if quality == "best":
                    format_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                else:
                    format_string = "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst"
            else:
                height = re.sub(r"\D", "", quality)
                if not height:
                    height = "1080"
                format_string = (
                    f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"best[height<={height}][ext=mp4]/"
                    f"bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                )
            postprocessors = []
        else:
            if quality in ["best", "worst"]:
                format_string = quality
            else:
                height = re.sub(r"\D", "", quality)
                if not height:
                    height = "1080"
                format_string = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/"
                    f"bestvideo+bestaudio/best"
                )
            postprocessors = []

        ydl_opts = {
            **self.ydl_opts_base,
            'format': format_string,
            'postprocessors': postprocessors,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(normalized_url, download=True)
                
                # Get the downloaded file path
                filename = ydl.prepare_filename(info)
                if format_selector == "mp3":
                    filename = filename.rsplit('.', 1)[0] + '.mp3'
                
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    return {
                        'success': True,
                        'file_path': filename,
                        'file_size': file_size,
                        'title': info.get('title', 'Unknown'),
                        'duration': info.get('duration', 0)
                    }
                else:
                    raise HTTPException(status_code=500, detail="Download completed but file not found")
                    
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")
    
    def download_playlist(self, url: str, format_selector: str = "best", quality: str = "best", 
                         start_index: int = 1, end_index: Optional[int] = None) -> Dict[str, Any]:
        """Download playlist videos"""
        normalized_url = self.normalize_url(url)
        
        playlist_indices = f"{start_index}:{end_index if end_index else ''}"
        
        # Configure format string based on preferences
        if format_selector == "mp3":
            format_string = "bestaudio/best"
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif format_selector == "mp4":
            if quality == "best":
                format_string = "best[ext=mp4]/best"
            elif quality == "worst":
                format_string = "worst[ext=mp4]/worst"
            else:
                height = quality.replace('p', '')
                format_string = f"best[height<={height}][ext=mp4]/best[height<={height}]/best[ext=mp4]/best"
            postprocessors = []
        else:
            if quality == "best":
                format_string = "best"
            elif quality == "worst":
                format_string = "worst"
            else:
                height = quality.replace('p', '')
                format_string = f"best[height<={height}]/best"
            postprocessors = []
        
        ydl_opts = {
            **self.ydl_opts_base,
            'format': format_string,
            'playlist_items': playlist_indices,
            'outtmpl': os.path.join(DOWNLOADS_DIR, '%(playlist)s/%(playlist_index)s - %(title)s.%(ext)s'),
            'postprocessors': postprocessors,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(normalized_url, download=True)
                
                downloaded_files = []
                total_size = 0
                
                if 'entries' in info:
                    for entry in info['entries']:
                        if entry:
                            filename = ydl.prepare_filename(entry)
                            if format_selector == "mp3":
                                filename = filename.rsplit('.', 1)[0] + '.mp3'
                            
                            if os.path.exists(filename):
                                file_size = os.path.getsize(filename)
                                downloaded_files.append({
                                    'title': entry.get('title', 'Unknown'),
                                    'file_path': filename,
                                    'file_size': file_size
                                })
                                total_size += file_size
                
                return {
                    'success': True,
                    'playlist_title': info.get('title', 'Unknown Playlist'),
                    'downloaded_count': len(downloaded_files),
                    'files': downloaded_files,
                    'total_size': total_size
                }
                
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Playlist download failed: {str(e)}")

# Initialize downloader
downloader = YouTubeDownloader()

@app.get("/")
async def root():
    return {
        "message": "YouTube Downloader API",
        "version": "1.0.0",
        "endpoints": {
            "GET /": "This endpoint",
            "GET /health": "Health check",
            "POST /video/info": "Get video information",
            "POST /video/download": "Download single video",
            "POST /playlist/download": "Download playlist",
            "GET /download/{filename}": "Download file",
            "GET /files": "List downloaded files"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "YouTube Downloader API is running"}

@app.post("/video/info")
async def get_video_info(request: VideoDownloadRequest):
    """Get video information without downloading"""
    try:
        info = downloader.get_video_info(request.url)
        
        # Extract and organize available formats
        video_formats = []
        audio_formats = []
        combined_formats = []
        
        for f in info.get('formats', []):
            format_info = {
                'format_id': f.get('format_id', ''),
                'ext': f.get('ext', ''),
                'resolution': f.get('resolution', 'Unknown'),
                'height': f.get('height', 0) if f.get('height') is not None else 0,
                'width': f.get('width', 0) if f.get('width') is not None else 0,
                'fps': f.get('fps', 0) if f.get('fps') is not None else 0,
                'filesize': f.get('filesize', 0) if f.get('filesize') is not None else 0,
                'vcodec': f.get('vcodec', 'none'),
                'acodec': f.get('acodec', 'none'),
                'tbr': f.get('tbr', 0) if f.get('tbr') is not None else 0,  # Total bitrate
                'vbr': f.get('vbr', 0) if f.get('vbr') is not None else 0,  # Video bitrate
                'abr': f.get('abr', 0) if f.get('abr') is not None else 0,  # Audio bitrate
            }
            
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                # Combined video+audio format
                combined_formats.append(format_info)
            elif f.get('vcodec') != 'none' and f.get('acodec') == 'none':
                # Video only format
                video_formats.append(format_info)
            elif f.get('vcodec') == 'none' and f.get('acodec') != 'none':
                # Audio only format
                audio_formats.append(format_info)
        
        # Sort formats by quality (height for video, bitrate for audio)
        combined_formats.sort(key=lambda x: ((x['height'] or 0), (x['tbr'] or 0)), reverse=True)
        video_formats.sort(key=lambda x: ((x['height'] or 0), (x['vbr'] or 0)), reverse=True)
        audio_formats.sort(key=lambda x: (x['abr'] or 0), reverse=True)
        
        # Get unique qualities available
        available_qualities = list(set([
            f"{f['height']}p" for f in combined_formats + video_formats 
            if f.get('height') and f['height'] > 0
        ]))
        available_qualities.sort(key=lambda x: int(x.replace('p', '')), reverse=True)
        
        # Extract relevant information
        video_info = VideoInfo(
            id=info.get('id', ''),
            title=info.get('title', 'Unknown'),
            duration=info.get('duration', 0),
            view_count=info.get('view_count', 0),
            upload_date=info.get('upload_date', ''),
            uploader=info.get('uploader', 'Unknown'),
            description=info.get('description', '')[:500] + "..." if info.get('description', '') else '',
            thumbnail=info.get('thumbnail', ''),
            formats=combined_formats[:10]  # Limit to first 10 formats
        )
        
        return {
            **video_info.dict(),
            'available_qualities': available_qualities,
            'combined_formats': combined_formats[:10],
            'video_formats': video_formats[:10],
            'audio_formats': audio_formats[:5]
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/video/download")
async def download_video(request: VideoDownloadRequest, background_tasks: BackgroundTasks):
    """Download a single video"""
    try:
        # Check if it's a playlist URL
        if downloader.is_playlist_url(request.url):
            raise HTTPException(
                status_code=400, 
                detail="Playlist URL detected. Use /playlist/download endpoint for playlists."
            )

        # Use the format_id directly if provided
        result = downloader.download_video(request.url, request.format, request.quality)

        filename = os.path.basename(result['file_path'])
        download_url = f"/download/{filename}"

        response = DownloadResponse(
            success=True,
            message=f"Video '{result['title']}' downloaded successfully.",
            file_path=result['file_path'],
            file_size=result['file_size'],
            download_url=download_url
        )

        # Clean up file after 1 hour
        background_tasks.add_task(cleanup_file, result['file_path'], delay=3600)

        return response

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/playlist/download")
async def download_playlist(request: PlaylistDownloadRequest, background_tasks: BackgroundTasks):
    """Download playlist videos"""
    try:
        result = downloader.download_playlist(
            request.url, 
            request.format, 
            request.quality,
            request.start_index,
            request.end_index
        )
        
        return {
            "success": True,
            "message": f"Playlist '{result['playlist_title']}' downloaded successfully",
            "playlist_title": result['playlist_title'],
            "downloaded_count": result['downloaded_count'],
            "total_size": result['total_size'],
            "files": [{
                "title": f['title'],
                "download_url": f"/download/{os.path.basename(f['file_path'])}",
                "file_size": f['file_size']
            } for f in result['files']]
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download a file"""
    file_path = os.path.join(DOWNLOADS_DIR, filename)
    
    # Also check in subdirectories (for playlist downloads)
    if not os.path.exists(file_path):
        for root, dirs, files in os.walk(DOWNLOADS_DIR):
            if filename in files:
                file_path = os.path.join(root, filename)
                break
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream'
    )

@app.get("/files")
async def list_files():
    """List all downloaded files"""
    files = []
    
    for root, dirs, filenames in os.walk(DOWNLOADS_DIR):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            file_size = os.path.getsize(file_path)
            relative_path = os.path.relpath(file_path, DOWNLOADS_DIR)
            
            files.append({
                "filename": filename,
                "path": relative_path,
                "size": file_size,
                "download_url": f"/download/{filename}"
            })
    
    return {"files": files, "total_files": len(files)}

async def cleanup_file(file_path: str, delay: int = 3600):
    """Clean up downloaded file after specified delay"""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logger.error(f"Failed to clean up file {file_path}: {e}")

@app.delete("/files/{filename}")
async def delete_file(filename: str):
    """Delete a specific file"""
    file_path = os.path.join(DOWNLOADS_DIR, filename)
    
    # Also check in subdirectories
    if not os.path.exists(file_path):
        for root, dirs, files in os.walk(DOWNLOADS_DIR):
            if filename in files:
                file_path = os.path.join(root, filename)
                break
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        os.remove(file_path)
        return {"success": True, "message": f"File {filename} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",  # Use localhost for local development
        port=8000,
        reload=True,
        workers=1
    )
