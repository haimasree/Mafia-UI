# Mafia Game Web Interface

This is a web-based interface for your Mafia game that replaces the need for two separate terminal windows. Players can now join and play the game through a single web browser interface.

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. File Structure

Make sure your project has this structure:
```
your-game-directory/
├── main.py                 # The FastAPI web server
├── templates/              # HTML templates
│   ├── home.html
│   ├── select_name.html
│   └── game.html
├── static/                 # CSS and other static files
│   └── style.css
├── games/                  # Your existing game directories
│   └── [game_id]/         # Individual game folders
├── requirements.txt
├── game_constants.py       # Your existing game files
├── game_status_checks.py
├── player_survey.py
└── README.md
```

### 3. Running the Server

Start the web server:
```bash
python main.py
```

The server will start on `http://127.0.0.1:8000`

### 4. Player Connection

Players should connect to your server using SSH with port forwarding:

```bash
ssh -L 8000:localhost:8000 username@your-server.com
```

Then they can open their web browser and go to:
```
http://localhost:8000
```

## How It Works

### For Players:

1. **Connect via SSH**: Use the SSH command with port forwarding
2. **Open Browser**: Navigate to `http://localhost:8000`
3. **Enter Game ID**: Type the game ID provided by the organizer
4. **Enter Real Name**: Provide your real name for the survey
5. **Get Character**: You'll be assigned a character name automatically
6. **Play**: Chat and vote all in the same interface!

### For Game Organizers:

The web interface integrates with your existing file-based game system. All the game logic, file reading/writing, and player management continues to work exactly as before.

## Features

- **Single Interface**: No more need for two terminal windows
- **Real-time Chat**: Messages appear instantly using WebSocket connections
- **Voting Interface**: Clean voting UI when it's time to vote
- **Role Display**: Players can see their role (Mafia/Bystander) clearly
- **Responsive Design**: Works on desktop and mobile browsers
- **Error Handling**: Clear error messages for invalid game IDs

## Technical Details

- **Backend**: FastAPI with WebSocket support for real-time features
- **Frontend**: HTML/CSS/JavaScript with WebSocket client
- **Integration**: Uses your existing game files and logic
- **Session Management**: Secure session handling with cookies

## Customization

You can customize the interface by:

1. **Styling**: Edit `static/style.css` to change colors, fonts, layout
2. **Templates**: Modify HTML files in `templates/` directory
3. **Game Logic**: The `main.py` file integrates with your existing game functions
4. **File Paths**: Update file paths in `main.py` to match your directory structure

## Troubleshooting

### Common Issues:

1. **Import Errors**: Make sure your existing game files are in the same directory
2. **Port Already in Use**: Change the port in `main.py` if 8000 is taken
3. **File Permissions**: Ensure the web server can read/write game files
4. **WebSocket Connection**: Check firewall settings if real-time features don't work

### Debug Mode:

To run in debug mode with auto-reload:
```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Security Notes

- The server only binds to localhost (127.0.0.1) for security
- Players must use SSH port forwarding to access the interface
- Session management prevents unauthorized access to games
- All game data remains on your server as before

## Next Steps

1. Test with a small group first
2. Customize the styling to match your preferences
3. Add any additional features you need
4. Consider adding player authentication if needed

Enjoy your improved Mafia game experience! 🎭
