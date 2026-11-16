USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[member_identity]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[member_identity](
	[member_id] [uniqueidentifier] NOT NULL,
	[scheme_id] [uniqueidentifier] NOT NULL,
	[member_name] [nvarchar](200) NULL,
	[id_number] [nvarchar](20) NULL,
	[gender] [nvarchar](1) NULL,
	[date_of_birth] [date] NULL,
	[annual_salary] [float] NULL,
	[match_method] [nvarchar](20) NULL,
	[match_confidence] [float] NULL,
	[created_at] [datetime2](7) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[member_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[member_identity] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
ALTER TABLE [dbo].[member_identity]  WITH CHECK ADD FOREIGN KEY([scheme_id])
REFERENCES [dbo].[scheme] ([scheme_id])
GO
