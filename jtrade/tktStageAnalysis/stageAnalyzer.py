import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import yfinance as yf
import tkinter as tk

class StageAnalysisSystem:
    def __init__(self, ticker, period='5y'):
        """Initialize with stock ticker and data period."""
        self.ticker = ticker
        self.period = period
        self.data = self._load_data()
        
    def _load_data(self):
        """Load historical price data."""
        stock = yf.Ticker(self.ticker)
        data = stock.history(period=self.period)
        return data
    
    def calculate_indicators(self, ma_short=50, ma_long=200, rsi_period=14):
        """Calculate technical indicators for stage analysis."""
        # Moving averages
        self.data['MA_Short'] = self.data['Close'].rolling(window=ma_short).mean()
        self.data['MA_Long'] = self.data['Close'].rolling(window=ma_long).mean()
        
        # RSI calculation
        delta = self.data['Close'].diff()
        gain = delta.where(delta > 0, 0).fillna(0)
        loss = -delta.where(delta < 0, 0).fillna(0)
        
        avg_gain = gain.rolling(window=rsi_period).mean()
        avg_loss = loss.rolling(window=rsi_period).mean()
        
        rs = avg_gain / avg_loss
        self.data['RSI'] = 100 - (100 / (1 + rs))
        
        # Volume analysis
        self.data['Volume_MA'] = self.data['Volume'].rolling(window=20).mean()
        
        return self.data
    
    def identify_stages(self):
        """Identify market stages based on technical indicators."""
        # Create a new column for stages
        self.data['Stage'] = np.nan
        
        # Stage 1: Accumulation (Basing/Bottoming)
        condition1 = (self.data['MA_Short'] > self.data['MA_Short'].shift(1)) & \
                    (self.data['MA_Long'] < self.data['MA_Long'].shift(1)) & \
                    (self.data['RSI'] > 50)
        
        # Stage 2: Markup (Uptrend)
        condition2 = (self.data['MA_Short'] > self.data['MA_Long']) & \
                    (self.data['MA_Short'] > self.data['MA_Short'].shift(1)) & \
                    (self.data['MA_Long'] > self.data['MA_Long'].shift(1))
        
        # Stage 3: Distribution (Topping)
        condition3 = (self.data['MA_Short'] < self.data['MA_Short'].shift(1)) & \
                    (self.data['MA_Long'] > self.data['MA_Long'].shift(1)) & \
                    (self.data['RSI'] < 50)
        
        # Stage 4: Decline (Downtrend)
        condition4 = (self.data['MA_Short'] < self.data['MA_Long']) & \
                    (self.data['MA_Short'] < self.data['MA_Short'].shift(1)) & \
                    (self.data['MA_Long'] < self.data['MA_Long'].shift(1))
        
        self.data.loc[condition1, 'Stage'] = 1
        self.data.loc[condition2, 'Stage'] = 2
        self.data.loc[condition3, 'Stage'] = 3
        self.data.loc[condition4, 'Stage'] = 4
        
        # Forward fill stages for continuous representation
        self.data['Stage'] = self.data['Stage'].fillna(method='ffill')
        
        return self.data
    
    def plot_stages(self):
        """Plot price chart with identified stages."""
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [3, 1, 1]})
        
        # Price and MA plot
        ax1.plot(self.data.index, self.data['Close'], label='Close Price', color='black', alpha=0.6)
        ax1.plot(self.data.index, self.data['MA_Short'], label=f'{self.data["MA_Short"].name} MA', color='blue')
        ax1.plot(self.data.index, self.data['MA_Long'], label=f'{self.data["MA_Long"].name} MA', color='red')
        
        # Color the background based on stages
        for stage in range(1, 5):
            mask = self.data['Stage'] == stage
            if mask.any():
                stage_color = {1: 'lightblue', 2: 'green', 3: 'yellow', 4: 'red'}
                ax1.fill_between(self.data.index, self.data['Close'].min(), self.data['Close'].max(), 
                                where=mask, color=stage_color[stage], alpha=0.2, 
                                label=f'Stage {stage}')
        
        ax1.set_title(f'Stage Analysis for {self.ticker}')
        ax1.set_ylabel('Price')
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        
        # RSI plot
        ax2.plot(self.data.index, self.data['RSI'], color='purple')
        ax2.axhline(y=70, color='red', linestyle='--', alpha=0.5)
        ax2.axhline(y=30, color='green', linestyle='--', alpha=0.5)
        ax2.axhline(y=50, color='black', linestyle='--', alpha=0.5)
        ax2.set_ylabel('RSI')
        ax2.grid(True, alpha=0.3)
        
        # Volume plot
        ax3.bar(self.data.index, self.data['Volume'], color='blue', alpha=0.5)
        ax3.plot(self.data.index, self.data['Volume_MA'], color='red')
        ax3.set_ylabel('Volume')
        ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        return fig
    
    def get_current_stage(self):
        """Return the current market stage."""
        current_stage = self.data['Stage'].iloc[-1]
        stage_descriptions = {
            1: "Accumulation (Bottoming/Basing)",
            2: "Markup (Uptrend/Bull)",
            3: "Distribution (Topping)",
            4: "Decline (Downtrend/Bear)"
        }
        
        return f"Current Stage: {int(current_stage)} - {stage_descriptions[current_stage]}"
    
    def generate_trading_signals(self):
        """Generate basic trading signals based on stage transitions."""
        self.data['Signal'] = 'Hold'
        
        # Buy signals: Entry into Stage 2 from Stage 1
        stage_2_entry = (self.data['Stage'] == 2) & (self.data['Stage'].shift(1) == 1)
        self.data.loc[stage_2_entry, 'Signal'] = 'Buy'
        
        # Sell signals: Entry into Stage 4 from Stage 3
        stage_4_entry = (self.data['Stage'] == 4) & (self.data['Stage'].shift(1) == 3)
        self.data.loc[stage_4_entry, 'Signal'] = 'Sell'
        
        return self.data[self.data['Signal'] != 'Hold']

def run_stage_analysis(ticker='AAPL'):
    """Run a complete stage analysis for the given ticker."""
    system = StageAnalysisSystem(ticker)
    system.calculate_indicators()
    system.identify_stages()
    
    # Plot the results
    fig = system.plot_stages()
    plt.show()
    
    # Print current stage
    print(system.get_current_stage())
    
    # Generate and print trading signals
    signals = system.generate_trading_signals()
    if not signals.empty:
        print("\nTrading Signals:")
        print(signals[['Close', 'Signal']])
    else:
        print("\nNo trading signals generated in the analyzed period.")
    
    return system

def perform_action():
    """Gets the text from the entry box and performs an action."""
    user_input = entry.get()
    print(f"You entered: {user_input}")
    # Here you would add the code to do something with the user_input
    result_label.config(text=f"Action triggered with: {user_input}")
    analysis = run_stage_analysis(user_input)

# Create the main window
window = tk.Tk()
window.title("StageAnalyzer")

# Create a label for instructions
instruction_label = tk.Label(window, text="Enter TKT:")
instruction_label.pack(pady=10)

# Create an entry box (text box)
entry = tk.Entry(window, width=30)
entry.pack(pady=5)

# Create a button
action_button = tk.Button(window, text="Generate Chart", command=perform_action)
action_button.pack(pady=10)

# Create a label to display a result (optional)
result_label = tk.Label(window, text="")
result_label.pack(pady=5)

# Start the Tkinter event loop
window.mainloop()

## Example usage
#if __name__ == "__main__":
#    analysis = run_stage_analysis('TSLA')