# MySky Weather Dashboard

A desktop weather dashboard built with Python and PyQt6, powered by the OpenWeatherMap API. This application provides a clean, modern interface to view current weather conditions, a 7-day forecast, and detailed hourly charts for temperature, precipitation, and wind speed. Also hosted in Straeamlit.

---

## Features

- **Current Weather:** Displays temperature, conditions, sunrise/sunset times, and location details.
- **Air Quality Index (AQI):** Shows the current AQI level and a breakdown of major pollutants.
- **7-Day Forecast:** A scrollable view of the forecast for the upcoming week.
- **Interactive Hourly Charts:** Detailed graphs for Temperature, Precipitation Probability, and Wind Speed for the next 48 hours.
- **Light & Dark Themes:** Toggle between a sleek dark mode and a clean light mode.
- **Metric & Imperial Units:** Switch between Celsius and Fahrenheit.

---

## Tech Stack

- **Language:** Python 
- **GUI Framework:** PyQt6
- **Charting Library:** pyqtgraph
- **API Communication:** requests
- **Environment Variables:** python-dotenv

---

## Setup and Installation

### 1. Clone the Repository

```bash
git clone [https://github.com/your-username/MySky-weather-app.git](https://github.com/your-username/MySky-weather-app.git)
cd MySky-weather-app

2. Create a Virtual Environment
It's recommended to run the application in a virtual environment.

# For Windows
python -m venv venv
venv\Scripts\activate

# For macOS/Linux
python3 -m venv venv
source venv/bin/activate

3. Install Dependencies
Install the required packages using the requirements.txt file.

pip install -r requirements.txt

API Key Configuration
This project requires an API key from OpenWeatherMap to fetch weather data. The application uses the One Call API 3.0, which is available under their free plan.

How to Get Your API Key
Sign Up: Create a free account on OpenWeatherMap.

Subscribe to One Call API: After logging in, go to the One Call API 3.0 page and click the "Get API key and subscribe" button in the "Free" plan section.

Generate Key: Navigate to the API keys tab in your user dashboard. Your default key should be listed here. You can generate additional keys if needed.

Copy the Key: Copy the generated API key. It may take a few minutes to become active.

How to Use Your API Key
The application loads the API key from a .env file.

Create a .env file in the root directory of the project (the same folder as main.py).

Add your API key to the .env file in the following format:

OPENWEATHER_API_KEY=your_api_key_here

Replace your_api_key_here with the key you copied from the OpenWeatherMap dashboard. The .gitignore file is already configured to prevent this file from being uploaded to GitHub.

Usage
Once the setup is complete and the API key is configured, run the application with the following command:

python main.py
